/*
 * HTTPS for a static arm64-linux program.
 *
 * Nexa's HTTP runtime on Linux does not link OpenSSL: it dlopen()s
 * "libssl.so.3" at run time and dlsym()s the dozen or so functions it needs.
 * A static program has no dynamic loader, so musl's dlopen is a stub that
 * always fails -- and it is a *weak* stub, as is __dlsym behind dlsym, so that
 * a program can supply its own. This file does: asked for libssl or
 * libcrypto, dlopen hands back a handle, and dlsym on it answers with the
 * functions below -- the OpenSSL calls Nexa makes, implemented on mbedTLS.
 * Anything else fails exactly as musl's stubs do.
 *
 * Only what Nexa's runtime calls is here, with the meaning it relies on:
 *   SSL_connect         1 on a completed handshake, <= 0 otherwise
 *   SSL_read/SSL_write  bytes moved; 0 at the end of the stream; < 0 on error
 *   SSL_get_verify_result  0 when the peer's certificate chains to a trusted
 *                       root and matches the host name; non-zero otherwise.
 *                       The handshake itself does not stop on a bad
 *                       certificate (mbedTLS "optional" mode); Nexa asks
 *                       here straight after SSL_connect and closes the
 *                       connection before sending anything if it is not 0.
 *
 * Trusted roots are the device's own: $SSL_CERT_FILE or $SSL_CERT_DIR if
 * set, else the bundle a Linux distribution keeps -- Debian, Ubuntu and
 * Raspberry Pi OS at /etc/ssl/certs/ca-certificates.crt -- parsed once and
 * shared by every connection.
 */
#include <dlfcn.h>
#include <errno.h>
#include <pthread.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>

#include "mbedtls/ctr_drbg.h"
#include "mbedtls/entropy.h"
#include "mbedtls/net_sockets.h"
#include "mbedtls/ssl.h"
#include "mbedtls/x509_crt.h"
#include "psa/crypto.h"

/* musl's own: sets what dlerror() reports. */
void __dl_seterr(const char *, ...);

/* --- one-time setup ------------------------------------------------------------ */

static pthread_once_t psa_once = PTHREAD_ONCE_INIT;
static void psa_init(void) { psa_crypto_init(); }

static pthread_once_t ca_once = PTHREAD_ONCE_INIT;
static mbedtls_x509_crt ca;
static int ca_loaded;

static int count_certs(const mbedtls_x509_crt *c) {
    int n = 0;
    for (; c && c->raw.len; c = c->next) n++;
    return n;
}

static void load_ca(void) {
    static const char *const bundles[] = {
        "/etc/ssl/certs/ca-certificates.crt",                /* Debian, Ubuntu, Raspberry Pi OS, Alpine */
        "/etc/pki/tls/certs/ca-bundle.crt",                  /* Fedora, RHEL */
        "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem", /* newer RHEL */
        "/etc/ssl/ca-bundle.pem",                            /* openSUSE */
        "/etc/ssl/cert.pem",                                 /* Alpine, Arch, Void */
        0,
    };
    mbedtls_x509_crt_init(&ca);
    /* parse_file/parse_path return how many certificates they could not
       read; a bundle with one odd entry still gives every other one. */
    const char *file = getenv("SSL_CERT_FILE");
    const char *dir = getenv("SSL_CERT_DIR");
    if (file && *file) mbedtls_x509_crt_parse_file(&ca, file);
    if (!count_certs(&ca) && dir && *dir) mbedtls_x509_crt_parse_path(&ca, dir);
    for (int i = 0; !count_certs(&ca) && bundles[i]; i++) mbedtls_x509_crt_parse_file(&ca, bundles[i]);
    if (!count_certs(&ca)) mbedtls_x509_crt_parse_path(&ca, "/etc/ssl/certs");
    ca_loaded = count_certs(&ca) > 0;
}

/* --- SSL_CTX and SSL --------------------------------------------------------------- */

typedef struct {
    mbedtls_ssl_config conf;
    mbedtls_entropy_context entropy;
    mbedtls_ctr_drbg_context drbg;
    int have_ca;
} nexa_ctx;

typedef struct {
    mbedtls_ssl_context ssl;
    nexa_ctx *ctx;
    int fd;
    int verify;
} nexa_ssl;

static const char method_marker = 0;

static int init_ssl(unsigned long opts, const void *settings) {
    (void)opts; (void)settings;
    pthread_once(&psa_once, psa_init);
    return 1;
}
static int library_init(void) { return init_ssl(0, 0); }
static const void *client_method(void) { return &method_marker; }

static void ctx_free(void *p) {
    nexa_ctx *c = p;
    if (!c) return;
    mbedtls_ssl_config_free(&c->conf);
    mbedtls_ctr_drbg_free(&c->drbg);
    mbedtls_entropy_free(&c->entropy);
    free(c);
}

static void *ctx_new(const void *method) {
    (void)method;
    pthread_once(&psa_once, psa_init);
    nexa_ctx *c = calloc(1, sizeof *c);
    if (!c) return 0;
    mbedtls_ssl_config_init(&c->conf);
    mbedtls_entropy_init(&c->entropy);
    mbedtls_ctr_drbg_init(&c->drbg);
    static const unsigned char pers[] = "nexa-https";
    if (mbedtls_ctr_drbg_seed(&c->drbg, mbedtls_entropy_func, &c->entropy, pers, sizeof pers - 1) != 0 ||
        mbedtls_ssl_config_defaults(&c->conf, MBEDTLS_SSL_IS_CLIENT, MBEDTLS_SSL_TRANSPORT_STREAM,
                                    MBEDTLS_SSL_PRESET_DEFAULT) != 0) {
        ctx_free(c);
        return 0;
    }
    mbedtls_ssl_conf_rng(&c->conf, mbedtls_ctr_drbg_random, &c->drbg);
    mbedtls_ssl_conf_authmode(&c->conf, MBEDTLS_SSL_VERIFY_OPTIONAL);
    return c;
}

static int ctx_default_verify_paths(void *p) {
    nexa_ctx *c = p;
    pthread_once(&ca_once, load_ca);
    if (!ca_loaded) return 0;
    mbedtls_ssl_conf_ca_chain(&c->conf, &ca, 0);
    c->have_ca = 1;
    return 1;
}

static int net_send(void *p, const unsigned char *buf, size_t len) {
    nexa_ssl *s = p;
    for (;;) {
        ssize_t n = send(s->fd, buf, len, MSG_NOSIGNAL);
        if (n >= 0) return (int)n;
        if (errno == EINTR) continue;
        if (errno == EAGAIN || errno == EWOULDBLOCK) return MBEDTLS_ERR_SSL_WANT_WRITE;
        if (errno == EPIPE || errno == ECONNRESET) return MBEDTLS_ERR_NET_CONN_RESET;
        return MBEDTLS_ERR_NET_SEND_FAILED;
    }
}

static int net_recv(void *p, unsigned char *buf, size_t len) {
    nexa_ssl *s = p;
    for (;;) {
        ssize_t n = recv(s->fd, buf, len, 0);
        if (n >= 0) return (int)n;
        if (errno == EINTR) continue;
        if (errno == EAGAIN || errno == EWOULDBLOCK) return MBEDTLS_ERR_SSL_WANT_READ;
        if (errno == ECONNRESET) return MBEDTLS_ERR_NET_CONN_RESET;
        return MBEDTLS_ERR_NET_RECV_FAILED;
    }
}

static void *ssl_new(void *ctx) {
    nexa_ssl *s = calloc(1, sizeof *s);
    if (!s) return 0;
    s->ctx = ctx;
    s->fd = -1;
    mbedtls_ssl_init(&s->ssl);
    if (mbedtls_ssl_setup(&s->ssl, &s->ctx->conf) != 0) {
        mbedtls_ssl_free(&s->ssl);
        free(s);
        return 0;
    }
    return s;
}

static void ssl_free(void *p) {
    nexa_ssl *s = p;
    if (!s) return;
    mbedtls_ssl_free(&s->ssl);
    free(s);
}

static int ssl_set_fd(void *p, int fd) {
    nexa_ssl *s = p;
    s->fd = fd;
    mbedtls_ssl_set_bio(&s->ssl, s, net_send, net_recv, 0);
    return 1;
}

static void ssl_set_verify(void *p, int mode, void *callback) {
    (void)callback;
    ((nexa_ssl *)p)->verify = mode != 0;
}

/* The host name is both SNI and the name the certificate must carry. */
static int ssl_set1_host(void *p, const char *host) {
    return mbedtls_ssl_set_hostname(&((nexa_ssl *)p)->ssl, host) == 0;
}

#define NEXA_SSL_CTRL_SET_TLSEXT_HOSTNAME 55
static long ssl_ctrl(void *p, int cmd, long larg, void *parg) {
    (void)larg;
    if (cmd == NEXA_SSL_CTRL_SET_TLSEXT_HOSTNAME && parg) return ssl_set1_host(p, (const char *)parg);
    return 0;
}

static int ssl_connect(void *p) {
    nexa_ssl *s = p;
    for (;;) {
        int r = mbedtls_ssl_handshake(&s->ssl);
        if (r == 0) return 1;
        if (r != MBEDTLS_ERR_SSL_WANT_READ && r != MBEDTLS_ERR_SSL_WANT_WRITE) return -1;
    }
}

static long ssl_get_verify_result(const void *p) {
    const nexa_ssl *s = p;
    if (!s->verify) return 0;
    if (!s->ctx->have_ca) return 20; /* X509_V_ERR_UNABLE_TO_GET_ISSUER_CERT_LOCALLY: nothing to trust */
    return mbedtls_ssl_get_verify_result(&s->ssl) == 0 ? 0 : 1;
}

static int ssl_write(void *p, const void *buf, int num) {
    nexa_ssl *s = p;
    if (num <= 0) return 0;
    for (;;) {
        int r = mbedtls_ssl_write(&s->ssl, buf, (size_t)num);
        if (r >= 0) return r;
        if (r != MBEDTLS_ERR_SSL_WANT_READ && r != MBEDTLS_ERR_SSL_WANT_WRITE) return -1;
    }
}

static int ssl_read(void *p, void *buf, int num) {
    nexa_ssl *s = p;
    if (num <= 0) return 0;
    for (;;) {
        int r = mbedtls_ssl_read(&s->ssl, buf, (size_t)num);
        if (r >= 0) return r;
        switch (r) {
        case MBEDTLS_ERR_SSL_WANT_READ:
        case MBEDTLS_ERR_SSL_WANT_WRITE:
#ifdef MBEDTLS_ERR_SSL_RECEIVED_NEW_SESSION_TICKET
        case MBEDTLS_ERR_SSL_RECEIVED_NEW_SESSION_TICKET: /* TLS 1.3: nothing to read yet */
#endif
            continue;
        case MBEDTLS_ERR_SSL_PEER_CLOSE_NOTIFY: /* the orderly end */
        case MBEDTLS_ERR_SSL_CONN_EOF:          /* closed without saying so, as many servers do */
        case MBEDTLS_ERR_NET_CONN_RESET:
            return 0;
        default:
            return -1;
        }
    }
}

static int ssl_shutdown(void *p) {
    mbedtls_ssl_close_notify(&((nexa_ssl *)p)->ssl);
    return 1;
}

/* --- dlopen / dlsym ------------------------------------------------------------------ */

static const char libssl_handle = 0;
static const char libcrypto_handle = 0;

static const struct {
    const char *name;
    void *fn;
} libssl_symbols[] = {
    {"OPENSSL_init_ssl", (void *)init_ssl},
    {"SSL_library_init", (void *)library_init},
    {"TLS_client_method", (void *)client_method},
    {"SSLv23_client_method", (void *)client_method},
    {"SSL_CTX_new", (void *)ctx_new},
    {"SSL_CTX_free", (void *)ctx_free},
    {"SSL_CTX_set_default_verify_paths", (void *)ctx_default_verify_paths},
    {"SSL_new", (void *)ssl_new},
    {"SSL_free", (void *)ssl_free},
    {"SSL_set_fd", (void *)ssl_set_fd},
    {"SSL_set_verify", (void *)ssl_set_verify},
    {"SSL_set1_host", (void *)ssl_set1_host},
    {"SSL_ctrl", (void *)ssl_ctrl},
    {"SSL_connect", (void *)ssl_connect},
    {"SSL_get_verify_result", (void *)ssl_get_verify_result},
    {"SSL_write", (void *)ssl_write},
    {"SSL_read", (void *)ssl_read},
    {"SSL_shutdown", (void *)ssl_shutdown},
};

static int named(const char *file, const char *lib) {
    const char *base = strrchr(file, '/');
    base = base ? base + 1 : file;
    return strncmp(base, lib, strlen(lib)) == 0;
}

void *dlopen(const char *file, int mode) {
    (void)mode;
    if (file && named(file, "libssl.so")) return (void *)&libssl_handle;
    if (file && named(file, "libcrypto.so")) return (void *)&libcrypto_handle;
    __dl_seterr("Dynamic loading not supported");
    return 0;
}

void *__dlsym(void *restrict handle, const char *restrict name, void *restrict ra) {
    (void)ra;
    if (handle == &libssl_handle) {
        for (size_t i = 0; i < sizeof libssl_symbols / sizeof libssl_symbols[0]; i++) {
            if (strcmp(libssl_symbols[i].name, name) == 0) return libssl_symbols[i].fn;
        }
    }
    __dl_seterr("Symbol not found: %s", name);
    return 0;
}
