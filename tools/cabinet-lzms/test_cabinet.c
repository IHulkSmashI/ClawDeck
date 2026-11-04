/* Test: load cabinet.dll from the given path, imported by ordinal like the FSR 4 XMX build does, and unpack
 * Compression API (LZMS buffered) files: test_cabinet.exe <cabinet.dll> <in> <out> [<in> <out> ...] */
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>

typedef BOOL (WINAPI *create_t)(DWORD, void *, HANDLE *);
typedef BOOL (WINAPI *decomp_t)(HANDLE, const void *, SIZE_T, void *, SIZE_T, SIZE_T *);
typedef BOOL (WINAPI *close_t)(HANDLE);

static void *slurp(const char *p, size_t *n)
{
    FILE *f = fopen(p, "rb"); if (!f) return NULL;
    fseek(f, 0, SEEK_END); *n = (size_t) ftell(f); rewind(f);
    void *b = malloc(*n); if (fread(b, 1, *n, f) != *n) { free(b); b = NULL; }
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
        size_t n; void *in = slurp(argv[i], &n);
        if (!in) { printf("%s: unreadable\n", argv[i]); return 1; }
        HANDLE h;
        if (!create(5, NULL, &h)) { printf("CreateDecompressor failed %lu\n", GetLastError()); return 1; }
        SIZE_T need = 0;
        BOOL r = decomp(h, in, n, NULL, 0, &need);                     /* size query, as callers do */
        DWORD e = GetLastError();
        void *out = malloc(need);
        SIZE_T got = 0;
        if (!decomp(h, in, n, out, need, &got)) { printf("%s: Decompress failed %lu\n", argv[i], GetLastError()); return 1; }
        close(h);
        FILE *f = fopen(argv[i + 1], "wb"); fwrite(out, 1, got, f); fclose(f);
        printf("%s: size query %s (error %lu, %zu bytes), unpacked %zu bytes\n", argv[i], r ? "TRUE" : "FALSE", e, (size_t) need, (size_t) got);
        free(in); free(out);
    }
    return 0;
}
