/*
 * cabinet.dll replacement for Wine/Proton: the decompressor half of Windows' Compression API (compressapi.h) for
 * COMPRESS_ALGORITHM_LZMS, which Wine does not implement. Enough for programs that unpack LZMS resources at run time
 * (the FSR 4 on XMX upscaler builds do, for their GPU kernels).
 *
 * Buffered mode (the default) reads Microsoft's container: a 24-byte header
 *   u32 magic 0xC0E5510A (bytes 0A 51 E5 C0) | u16 header size (24) | u8 (unchecked) | u8 algorithm
 *   u64 uncompressed size | u64 block size
 * then for each block of up to <block size> output bytes: u32 stored size, and that many bytes, LZMS-compressed
 * (or the block itself when the stored size equals its output size). COMPRESS_RAW (0x20000000) takes one LZMS block
 * and needs the exact output size.
 *
 * The compressor half (CreateCompressor ... CloseCompressor, ordinals 30-35) is there so the export table matches
 * Windows', and fails with ERROR_NOT_SUPPORTED.
 *
 * LZMS decoding: wimlib's lzms_decompress.c by Eric Biggers (LGPL-2.1+). This file: LGPL-2.1+ as well.
 */
#include <windows.h>
#include <stdint.h>
#include <string.h>
#include "wimlib/decompressor_ops.h"

#define COMPRESS_ALGORITHM_LZMS 5
#define COMPRESS_RAW            0x20000000u
#define CAB_MAGIC               0xC0E5510Au
#define CAB_HEADER              24
#define LZMS_MAX_BLOCK          (64u << 20)   /* LZMS allows blocks up to 64 MiB */

typedef struct { void *ctx; void *(*alloc)(void *, SIZE_T); void (*free)(void *, void *); } COMPRESS_ALLOCATION_ROUTINES;

struct cab_decomp {
    uint32_t magic;          /* 'CABd' while open */
    int raw;
    void *lzms;              /* wimlib decompressor, made for the largest block seen so far */
    size_t lzms_max;
};
#define HANDLE_MAGIC 0x64424143u

void *wimlib_aligned_malloc(size_t size, size_t alignment) { return _aligned_malloc(size, alignment < sizeof(void *) ? sizeof(void *) : alignment); }
void wimlib_aligned_free(void *p) { _aligned_free(p); }

static int lzms_for(struct cab_decomp *d, size_t block)
{
    if (d->lzms && d->lzms_max >= block) return 1;
    if (d->lzms) lzms_decompressor_ops.free_decompressor(d->lzms);
    d->lzms = NULL; d->lzms_max = 0;
    if (lzms_decompressor_ops.create_decompressor(block, &d->lzms)) return 0;
    d->lzms_max = block;
    return 1;
}

static BOOL fail(DWORD err) { SetLastError(err); return FALSE; }

BOOL WINAPI CreateDecompressor(DWORD algorithm, COMPRESS_ALLOCATION_ROUTINES *routines, HANDLE *out)
{
    (void) routines;   /* the algorithm's working memory comes from the process heap */
    if (!out) return fail(ERROR_INVALID_PARAMETER);
    if ((algorithm & ~COMPRESS_RAW) != COMPRESS_ALGORITHM_LZMS) return fail(ERROR_NOT_SUPPORTED);
    struct cab_decomp *d = HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, sizeof *d);
    if (!d) return fail(ERROR_NOT_ENOUGH_MEMORY);
    d->magic = HANDLE_MAGIC;
    d->raw = (algorithm & COMPRESS_RAW) != 0;
    *out = d;
    return TRUE;
}

BOOL WINAPI Decompress(HANDLE h, const void *in, SIZE_T in_size, void *out, SIZE_T out_size, SIZE_T *out_written)
{
    struct cab_decomp *d = h;
    const uint8_t *src = in;
    if (!d || d->magic != HANDLE_MAGIC || (!in && in_size)) return fail(ERROR_INVALID_HANDLE);

    if (d->raw) {
        if (out_written) *out_written = out_size;
        if (!out || !out_size || out_size > LZMS_MAX_BLOCK) return fail(ERROR_INVALID_PARAMETER);
        if (!lzms_for(d, out_size)) return fail(ERROR_NOT_ENOUGH_MEMORY);
        if (lzms_decompressor_ops.decompress(src, in_size, out, out_size, d->lzms)) return fail(ERROR_BAD_COMPRESSION_BUFFER);
        return TRUE;
    }

    if (in_size < CAB_HEADER) return fail(ERROR_BAD_COMPRESSION_BUFFER);
    uint32_t magic; uint16_t hsize; uint64_t total, block;
    memcpy(&magic, src, 4); memcpy(&hsize, src + 4, 2); memcpy(&total, src + 8, 8); memcpy(&block, src + 16, 8);
    if (magic != CAB_MAGIC || hsize != CAB_HEADER || src[7] != COMPRESS_ALGORITHM_LZMS) return fail(ERROR_BAD_COMPRESSION_BUFFER);
    if (out_written) *out_written = (SIZE_T) total;
    if (!out || out_size < total) return fail(ERROR_INSUFFICIENT_BUFFER);   /* the caller asks for the size this way */
    if (!block || block > LZMS_MAX_BLOCK) return fail(ERROR_BAD_COMPRESSION_BUFFER);
    if (!lzms_for(d, (size_t) block)) return fail(ERROR_NOT_ENOUGH_MEMORY);

    size_t pos = CAB_HEADER, done = 0;
    uint8_t *dst = out;
    while (done < total) {
        uint32_t stored;
        if (in_size - pos < 4) return fail(ERROR_BAD_COMPRESSION_BUFFER);
        memcpy(&stored, src + pos, 4); pos += 4;
        size_t want = (size_t) (total - done < block ? total - done : block);
        if (stored > in_size - pos) return fail(ERROR_BAD_COMPRESSION_BUFFER);
        if (stored == want) memcpy(dst + done, src + pos, want);
        else if (lzms_decompressor_ops.decompress(src + pos, stored, dst + done, want, d->lzms)) return fail(ERROR_BAD_COMPRESSION_BUFFER);
        pos += stored; done += want;
    }
    return TRUE;
}

BOOL WINAPI CloseDecompressor(HANDLE h)
{
    struct cab_decomp *d = h;
    if (!d || d->magic != HANDLE_MAGIC) return fail(ERROR_INVALID_HANDLE);
    if (d->lzms) lzms_decompressor_ops.free_decompressor(d->lzms);
    d->magic = 0;
    HeapFree(GetProcessHeap(), 0, d);
    return TRUE;
}

BOOL WINAPI ResetDecompressor(HANDLE h)
{
    struct cab_decomp *d = h;
    return d && d->magic == HANDLE_MAGIC ? TRUE : fail(ERROR_INVALID_HANDLE);
}

BOOL WINAPI SetDecompressorInformation(HANDLE h, int cls, const void *info, SIZE_T size)
{ (void) h; (void) cls; (void) info; (void) size; return fail(ERROR_NOT_SUPPORTED); }

BOOL WINAPI QueryDecompressorInformation(HANDLE h, int cls, void *info, SIZE_T size)
{ (void) h; (void) cls; (void) info; (void) size; return fail(ERROR_NOT_SUPPORTED); }

/* The compressor half: not implemented (nothing ClawDeck supports compresses), so a caller gets a clean failure. */
BOOL WINAPI CreateCompressor(DWORD algorithm, COMPRESS_ALLOCATION_ROUTINES *routines, HANDLE *out)
{ (void) algorithm; (void) routines; if (out) *out = NULL; return fail(ERROR_NOT_SUPPORTED); }

BOOL WINAPI SetCompressorInformation(HANDLE h, int cls, const void *info, SIZE_T size)
{ (void) h; (void) cls; (void) info; (void) size; return fail(ERROR_NOT_SUPPORTED); }

BOOL WINAPI QueryCompressorInformation(HANDLE h, int cls, void *info, SIZE_T size)
{ (void) h; (void) cls; (void) info; (void) size; return fail(ERROR_NOT_SUPPORTED); }

BOOL WINAPI Compress(HANDLE h, const void *in, SIZE_T in_size, void *out, SIZE_T out_size, SIZE_T *out_written)
{ (void) h; (void) in; (void) in_size; (void) out; (void) out_size; if (out_written) *out_written = 0; return fail(ERROR_NOT_SUPPORTED); }

BOOL WINAPI ResetCompressor(HANDLE h)
{ (void) h; return fail(ERROR_NOT_SUPPORTED); }

BOOL WINAPI CloseCompressor(HANDLE h)
{ (void) h; return fail(ERROR_NOT_SUPPORTED); }

BOOL WINAPI DllMain(HINSTANCE inst, DWORD reason, void *reserved)
{ (void) reserved; if (reason == DLL_PROCESS_ATTACH) DisableThreadLibraryCalls(inst); return TRUE; }
