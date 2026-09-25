/*
 * mbedTLS settings for arm64-linux, on top of mbedTLS's default configuration
 * (mbedtls/mbedtls_config.h: TLS 1.2 and 1.3, the usual ciphers, X.509).
 * Named by -DMBEDTLS_USER_CONFIG_FILE in target.json.
 */

/* A Nexa program can make HTTPS requests from several threads at once, and
   the PSA crypto core behind TLS 1.3 keeps shared state. */
#define MBEDTLS_THREADING_C
#define MBEDTLS_THREADING_PTHREAD
