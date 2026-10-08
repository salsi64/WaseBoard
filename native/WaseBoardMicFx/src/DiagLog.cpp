#include "DiagLog.h"

#include <stdarg.h>
#include <stdio.h>
#include <wchar.h>

namespace
{
    SRWLOCK g_lock = SRWLOCK_INIT;
    volatile LONG g_processInfoWritten = 0;
    constexpr LONGLONG kMaxLogBytes = 512 * 1024;

    // %ProgramData%\WaseBoard\MicFx, créé au besoin (LOCAL SERVICE fait partie des Utilisateurs,
    // qui peuvent créer des dossiers sous ProgramData).
    bool LogPaths(wchar_t* logPath, wchar_t* oldPath, size_t count)
    {
        wchar_t root[MAX_PATH];
        const DWORD n = GetEnvironmentVariableW(L"ProgramData", root, MAX_PATH);
        if (n == 0 || n >= MAX_PATH) wcscpy_s(root, L"C:\\ProgramData");

        wchar_t dir[MAX_PATH];
        swprintf_s(dir, L"%s\\WaseBoard", root);
        CreateDirectoryW(dir, nullptr);
        swprintf_s(dir, L"%s\\WaseBoard\\MicFx", root);
        CreateDirectoryW(dir, nullptr);

        swprintf_s(logPath, count, L"%s\\apo.log", dir);
        swprintf_s(oldPath, count, L"%s\\apo.old.log", dir);
        return true;
    }

    void AppendUtf8(const wchar_t* line)
    {
        wchar_t logPath[MAX_PATH], oldPath[MAX_PATH];
        if (!LogPaths(logPath, oldPath, MAX_PATH)) return;

        WIN32_FILE_ATTRIBUTE_DATA attributes;
        if (GetFileAttributesExW(logPath, GetFileExInfoStandard, &attributes))
        {
            const LONGLONG size = (static_cast<LONGLONG>(attributes.nFileSizeHigh) << 32) | attributes.nFileSizeLow;
            if (size > kMaxLogBytes) MoveFileExW(logPath, oldPath, MOVEFILE_REPLACE_EXISTING);
        }

        HANDLE file = CreateFileW(logPath, FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            nullptr, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
        if (file == INVALID_HANDLE_VALUE) return;

        char utf8[2048];
        const int bytes = WideCharToMultiByte(CP_UTF8, 0, line, -1, utf8, sizeof(utf8), nullptr, nullptr);
        if (bytes > 1)
        {
            DWORD written = 0;
            WriteFile(file, utf8, static_cast<DWORD>(bytes - 1), &written, nullptr);
        }
        CloseHandle(file);
    }
}

namespace DiagLog
{
    void Write(const wchar_t* format, ...)
    {
        wchar_t message[900];
        va_list args;
        va_start(args, format);
        _vsnwprintf_s(message, _TRUNCATE, format, args);
        va_end(args);

        SYSTEMTIME t;
        GetLocalTime(&t);
        wchar_t line[1024];
        swprintf_s(line, L"%04u-%02u-%02u %02u:%02u:%02u.%03u [pid %lu tid %lu] %s\r\n",
            t.wYear, t.wMonth, t.wDay, t.wHour, t.wMinute, t.wSecond, t.wMilliseconds,
            GetCurrentProcessId(), GetCurrentThreadId(), message);

        OutputDebugStringW(line);
        AcquireSRWLockExclusive(&g_lock);
        AppendUtf8(line);
        ReleaseSRWLockExclusive(&g_lock);
    }

    void WriteProcessInfoOnce(HMODULE self)
    {
        if (InterlockedExchange(&g_processInfoWritten, 1) != 0) return;

        wchar_t exe[MAX_PATH] = L"?", dll[MAX_PATH] = L"?";
        GetModuleFileNameW(nullptr, exe, MAX_PATH);
        GetModuleFileNameW(self, dll, MAX_PATH);

        DWORD session = 0;
        ProcessIdToSessionId(GetCurrentProcessId(), &session);

        wchar_t account[256] = L"?";
        DWORD integrity = 0, isAppContainer = 0;
        HANDLE token = nullptr;
        if (OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token))
        {
            BYTE buffer[512];
            DWORD size = 0;
            if (GetTokenInformation(token, TokenUser, buffer, sizeof(buffer), &size))
            {
                wchar_t name[128], domain[128];
                DWORD nameLen = 128, domainLen = 128;
                SID_NAME_USE use;
                if (LookupAccountSidW(nullptr, reinterpret_cast<TOKEN_USER*>(buffer)->User.Sid, name, &nameLen, domain, &domainLen, &use))
                    swprintf_s(account, L"%s\\%s", domain, name);
            }
            if (GetTokenInformation(token, TokenIntegrityLevel, buffer, sizeof(buffer), &size))
            {
                PSID sid = reinterpret_cast<TOKEN_MANDATORY_LABEL*>(buffer)->Label.Sid;
                integrity = *GetSidSubAuthority(sid, *GetSidSubAuthorityCount(sid) - 1);
            }
            GetTokenInformation(token, TokenIsAppContainer, &isAppContainer, sizeof(isAppContainer), &size);
            CloseHandle(token);
        }

        Write(L"=== Chargé par %s (session %lu, compte %s, intégrité 0x%lX, AppContainer %lu) — DLL %s",
            exe, session, account, integrity, isAppContainer, dll);
    }

    const wchar_t* GuidToString(REFGUID guid, wchar_t* buffer, size_t count)
    {
        swprintf_s(buffer, count, L"{%08lX-%04X-%04X-%02X%02X-%02X%02X%02X%02X%02X%02X}",
            guid.Data1, guid.Data2, guid.Data3, guid.Data4[0], guid.Data4[1], guid.Data4[2],
            guid.Data4[3], guid.Data4[4], guid.Data4[5], guid.Data4[6], guid.Data4[7]);
        return buffer;
    }
}
