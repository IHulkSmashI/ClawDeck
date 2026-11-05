/* Test: load cabinet.dll from the given path, imported by ordinal like the FSR 4 XMX build does (40 / 43 / 45), and
 * unpack Compression API LZMS data:
 *   test_cabinet.exe <cabinet.dll> <in> <out> [<in> <out> ...]
 * <in> is a buffered container (Windows' default), or raw:<size>:<file> for one raw LZMS block (COMPRESS_RAW).
 * For a buffered one it asks for the size first (out NULL), as callers do, and checks the answer.
 * Exit status 0 only if every file unpacked. License: LGPL-2.1-or-later. */
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define COMPRESS_RAW 0x20000000u
typedef BOOL (WINAPI *create_t)(DWORD, void *, HANDLE *);
typedef BOOL (WINAPI *decomp_t)(HANDLE, const void *, SIZE_T, void *, SIZE_T, SIZE_T *);
typedef BOOL (WINAPI *close_t)(HANDLE);

static void *slurp(const char *p, size_t *n)
{
    FILE *f = fopen(p, "rb"); if (!f) return NULL;
    fseek(f, 0, SEEK_END); *n = (size_t) ftell(f); rewind(f);
    void *b = malloc(*n ? *n : 1); if (fread(b, 1, *n, f) != *n) { free(b); b = NULL; }
    fclose(f); return b;
}

int main(int argc, char **argv)
{
    if (argc < 4 || argc % 2) { fprintf(stderr, "usage: %s cabinet.dll in out [in out ...]\n", argv[0]); return 2; }
    HMODULE m = LoadLibraryA(argv[1]);
    if (!m) { printf("LoadLibrary failed %lu\n", GetLastError()); return 1; }
    create_t create = (create_t) GetProcAddress(m, (LPCSTR) 40);
    decomp_t decomp = (decomp_t) GetProcAddress(m, (LPCSTR) 43);
    close_t  close  = (close_t)  GetProcAddress(m, (LPCSTR) 45);
    if (!create || !decomp || !close) { printf("ordinals 40/43/45 missing\n"); return 1; }
    for (int i = 2; i + 1 < argc; i += 2) {
        const char *path = argv[i];
        SIZE_T raw = 0;
        if (!strncmp(path, "raw:", 4)) {
            char *end; raw = (SIZE_T) strtoull(path + 4, &end, 10);
            if (*end != ':' || !raw) { printf("%s: want raw:<size>:<file>\n", path); return 2; }
            path = end + 1;
        }
        size_t n; void *in = slurp(path, &n);
        if (!in) { printf("%s: unreadable\n", path); return 1; }
        HANDLE h;
        if (!create(5 | (raw ? COMPRESS_RAW : 0), NULL, &h)) { printf("CreateDecompressor failed %lu\n", GetLastError()); return 1; }
        SIZE_T need = raw;
        if (!raw) {
            SetLastError(0);
            BOOL r = decomp(h, in, n, NULL, 0, &need);                 /* size query */
            DWORD e = GetLastError();
            if (r || e != ERROR_INSUFFICIENT_BUFFER) { printf("%s: size query gave %d / error %lu, want FALSE / 122\n", path, r, e); return 1; }
        }
        void *out = malloc(need ? need : 1);
        SIZE_T got = 0;
        if (!decomp(h, in, n, out, need, &got)) { printf("%s: Decompress failed %lu\n", path, GetLastError()); return 1; }
        if (got != need) { printf("%s: unpacked %zu bytes, size query said %zu\n", path, (size_t) got, (size_t) need); return 1; }
        if (!close(h)) { printf("CloseDecompressor failed %lu\n", GetLastError()); return 1; }
        FILE *f = fopen(argv[i + 1], "wb"); fwrite(out, 1, got, f); fclose(f);
        printf("%s: %s, unpacked %zu bytes\n", path, raw ? "raw" : "buffered", (size_t) got);
        free(in); free(out);
    }
    return 0;
}
