#include <windows.h>
#include <stdio.h>
int main(int c, char **v) {
    HMODULE m = LoadLibraryA(v[1]); if (!m) { printf("load failed %lu\n", GetLastError()); return 1; }
    const char *names[] = { "FDICreate", "FDIIsCabinet", "FCICreate", "Extract", "DllGetVersion" };
    for (int i = 0; i < 5; i++) { FARPROC p = GetProcAddress(m, names[i]); HMODULE owner = 0; char path[MAX_PATH] = "-";
        if (p && GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT, (LPCSTR) p, &owner)) GetModuleFileNameA(owner, path, MAX_PATH);
        printf("%-14s %s  in %s\n", names[i], p ? "ok" : "MISSING", path); }
    printf("ordinal 40 %s\n", GetProcAddress(m, (LPCSTR) 40) ? "ok" : "MISSING");
    return 0; }
