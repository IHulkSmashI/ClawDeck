/* Test vectors for cabinet.dll, made with wimlib's own LZMS compressor (libwim, native Linux build):
 *   mkvector <in> <block size> <out.lzms>   Microsoft's buffered Compression API container, as CreateDecompressor
 *                                           without COMPRESS_RAW reads it (see ../cabinet.c for the layout); a block
 *                                           LZMS doesn't shrink is stored as is, as Windows does
 *   mkvector <in> raw <out.raw>             one raw LZMS block (COMPRESS_RAW), for an input of up to 64 MiB
 * Build: cc -O2 -o mkvector mkvector.c -lwim
 * License: LGPL-2.1-or-later, as the rest of cabinet-lzms. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wimlib.h>

static void *slurp(const char *p, size_t *n)
{
    FILE *f = fopen(p, "rb"); if (!f) return NULL;
    fseek(f, 0, SEEK_END); *n = (size_t) ftell(f); rewind(f);
    void *b = malloc(*n ? *n : 1); if (fread(b, 1, *n, f) != *n) { free(b); b = NULL; }
    fclose(f); return b;
}

static void put(FILE *f, const void *p, size_t n) { if (fwrite(p, 1, n, f) != n) { perror("write"); exit(1); } }

int main(int argc, char **argv)
{
    if (argc != 4) { fprintf(stderr, "usage: %s in <block size>|raw out\n", argv[0]); return 2; }
    size_t n; uint8_t *in = slurp(argv[1], &n);
    if (!in) { perror(argv[1]); return 1; }
    int raw = !strcmp(argv[2], "raw");
    size_t block = raw ? n : strtoul(argv[2], NULL, 0);
    if (!block) { fprintf(stderr, "block size must be > 0\n"); return 2; }
    struct wimlib_compressor *c;
    if (wimlib_create_compressor(WIMLIB_COMPRESSION_TYPE_LZMS, block, 0, &c)) { fprintf(stderr, "no LZMS compressor\n"); return 1; }
    uint8_t *buf = malloc(block + 64);
    FILE *f = fopen(argv[3], "wb"); if (!f) { perror(argv[3]); return 1; }
    if (raw) {
        size_t got = wimlib_compress(in, n, buf, block + 64, c);
        if (!got) { fprintf(stderr, "raw: LZMS didn't fit\n"); return 1; }
        put(f, buf, got);
        printf("%s: %zu -> %zu bytes (raw LZMS)\n", argv[1], n, got);
    } else {
        uint8_t h[24] = {0x0A, 0x51, 0xE5, 0xC0, 24, 0, 0, 5};   /* magic, header size, -, algorithm LZMS */
        uint64_t total = n, bs = block;
        memcpy(h + 8, &total, 8); memcpy(h + 16, &bs, 8);
        put(f, h, sizeof h);
        size_t stored_blocks = 0, blocks = 0;
        for (size_t pos = 0; pos < n; pos += block, blocks++) {
            size_t want = n - pos < block ? n - pos : block;
            size_t got = wimlib_compress(in + pos, want, buf, want - 1, c);   /* 0: not smaller */
            uint32_t sz = (uint32_t) (got ? got : want);
            put(f, &sz, 4);
            put(f, got ? buf : in + pos, sz);
            stored_blocks += !got;
        }
        printf("%s: %zu bytes in %zu block(s) of %zu, %zu stored as is -> %ld bytes\n", argv[1], n, blocks, block,
               stored_blocks, ftell(f));
    }
    fclose(f);
    wimlib_free_compressor(c);
    return 0;
}
