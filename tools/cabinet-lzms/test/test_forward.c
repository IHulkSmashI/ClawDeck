/* Test: cabinet.dll's export table. Wine's own functions resolve in cabinet_wine.dll (the forwards), the
 * decompressor is ours, the compressor stubs and other algorithms fail with ERROR_NOT_SUPPORTED, bad input fails
 * cleanly. test_forward.exe <cabinet.dll>; exit status 0 only if all of it holds. License: LGPL-2.1-or-later. */
#include <windows.h>
#include <stdio.h>
#include <string.h>

typedef BOOL (WINAPI *create_t)(DWORD, void *, HANDLE *);
typedef BOOL (WINAPI *decomp_t)(HANDLE, const void *, SIZE_T, void *, SIZE_T, SIZE_T *);
static int bad;
static void check(int ok, const char *what) { printf("%-58s %s\n", what, ok ? "ok" : "FAIL"); bad += !ok; }

int main(int c, char **v)
{
    if (c != 2) { fprintf(stderr, "usage: %s cabinet.dll\n", v[0]); return 2; }
    HMODULE m = LoadLibraryA(v[1]); if (!m) { printf("load failed %lu\n", GetLastError()); return 1; }
    const char *names[] = { "FDICreate", "FDIIsCabinet", "FDICopy", "FCICreate", "Extract", "DllGetVersion" };
    for (int i = 0; i < 6; i++) {
        FARPROC p = GetProcAddress(m, names[i]); HMODULE owner = 0; char path[MAX_PATH] = "-", what[96];
        if (p && GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT, (LPCSTR) p, &owner))
            GetModuleFileNameA(owner, path, MAX_PATH);
        const char *base = strrchr(path, '\\'); base = base ? base + 1 : path;
        snprintf(what, sizeof what, "%s forwarded (in %s)", names[i], base);
        check(p && !_stricmp(base, "cabinet_wine.dll"), what);
    }
    for (int o = 30; o <= 45; o++) {
        if (o > 35 && o < 40) continue;
        char what[64]; snprintf(what, sizeof what, "ordinal %d exported", o);
        check(GetProcAddress(m, (LPCSTR) (ULONG_PTR) o) != NULL, what);
    }
    create_t cc = (create_t) GetProcAddress(m, "CreateCompressor"), cd = (create_t) GetProcAddress(m, "CreateDecompressor");
    decomp_t dd = (decomp_t) GetProcAddress(m, "Decompress");
    HANDLE h = (HANDLE) 1;
    SetLastError(0); check(cc && !cc(5, NULL, &h) && GetLastError() == ERROR_NOT_SUPPORTED, "CreateCompressor: FALSE, ERROR_NOT_SUPPORTED");
    SetLastError(0); check(cd && !cd(4 /* XPRESS_HUFF */, NULL, &h) && GetLastError() == ERROR_NOT_SUPPORTED, "CreateDecompressor(XPRESS_HUFF): ERROR_NOT_SUPPORTED");
    check(cd && cd(5, NULL, &h), "CreateDecompressor(LZMS)");
    unsigned char junk[64] = { 1, 2, 3 }, out[64]; SIZE_T got;
    SetLastError(0); check(dd && !dd(h, junk, sizeof junk, out, sizeof out, &got) && GetLastError() == ERROR_BAD_COMPRESSION_BUFFER, "Decompress(junk): ERROR_BAD_COMPRESSION_BUFFER");
    return bad ? 1 : 0;
}
