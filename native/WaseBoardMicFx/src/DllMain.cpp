// Point d'entrée COM de la DLL : une seule classe (CWaseBoardMicFx). L'inscription (CLSID,
// AudioEngine\AudioProcessingObjects, FxProperties du micro) est faite par WaseBoard lui-même
// (MicFxSetup.cs), pas par DllRegisterServer : une seule source de vérité.

#include "WaseBoardMicFx.h"
#include "DiagLog.h"

#include <new>

HMODULE g_dllModule = nullptr;

namespace
{
    volatile LONG g_serverLocks = 0;

    class CClassFactory final : public IClassFactory
    {
    public:
        STDMETHODIMP QueryInterface(REFIID riid, void** ppv) override
        {
            if (ppv == nullptr) return E_POINTER;
            if (riid == __uuidof(IUnknown) || riid == __uuidof(IClassFactory))
            {
                *ppv = static_cast<IClassFactory*>(this);
                return S_OK;
            }
            *ppv = nullptr;
            wchar_t iid[40];
            DiagLog::Write(L"Fabrique : interface non prise en charge %s", DiagLog::GuidToString(riid, iid, 40));
            return E_NOINTERFACE;
        }

        // Objet statique : durée de vie liée à la DLL.
        STDMETHODIMP_(ULONG) AddRef() override { return 2; }
        STDMETHODIMP_(ULONG) Release() override { return 1; }

        STDMETHODIMP CreateInstance(IUnknown* pUnkOuter, REFIID riid, void** ppv) override
        {
            if (ppv == nullptr) return E_POINTER;
            *ppv = nullptr;

            wchar_t iid[40];
            DiagLog::GuidToString(riid, iid, 40);

            // Agrégation COM : l'agrégateur doit demander IUnknown (il reçoit l'IUnknown non délégant).
            if (pUnkOuter != nullptr && riid != __uuidof(IUnknown))
            {
                DiagLog::Write(L"CreateInstance(%s, agrégé) -> CLASS_E_NOAGGREGATION", iid);
                return CLASS_E_NOAGGREGATION;
            }

            auto* apo = new (std::nothrow) CWaseBoardMicFx(pUnkOuter);
            if (apo == nullptr) return E_OUTOFMEMORY;
            IUnknown* inner = apo->NonDelegatingUnknown();
            const HRESULT hr = inner->QueryInterface(riid, ppv);
            inner->Release(); // référence initiale de la construction

            DiagLog::Write(L"CreateInstance(%s, %s) -> 0x%08lX", iid, pUnkOuter != nullptr ? L"agrégé" : L"non agrégé", hr);
            return hr;
        }

        STDMETHODIMP LockServer(BOOL fLock) override
        {
            if (fLock) InterlockedIncrement(&g_serverLocks);
            else InterlockedDecrement(&g_serverLocks);
            return S_OK;
        }
    };

    CClassFactory g_classFactory;
}

BOOL WINAPI DllMain(HINSTANCE hInstance, DWORD reason, LPVOID reserved)
{
    UNREFERENCED_PARAMETER(reserved);
    if (reason == DLL_PROCESS_ATTACH)
    {
        g_dllModule = hInstance;
        DisableThreadLibraryCalls(hInstance);
    }
    return TRUE;
}

_Check_return_
STDAPI DllGetClassObject(_In_ REFCLSID rclsid, _In_ REFIID riid, _Outptr_ LPVOID FAR* ppv)
{
    if (ppv == nullptr) return E_POINTER;
    *ppv = nullptr;
    DiagLog::WriteProcessInfoOnce(g_dllModule);
    const HRESULT hr = rclsid != CLSID_WaseBoardMicFx
        ? CLASS_E_CLASSNOTAVAILABLE
        : g_classFactory.QueryInterface(riid, ppv);

    wchar_t clsid[40], iid[40];
    DiagLog::Write(L"DllGetClassObject(%s, %s) -> 0x%08lX",
        DiagLog::GuidToString(rclsid, clsid, 40), DiagLog::GuidToString(riid, iid, 40), hr);
    return hr;
}

__control_entrypoint(DllExport)
STDAPI DllCanUnloadNow(void)
{
    return (g_dllObjectCount == 0 && g_serverLocks == 0) ? S_OK : S_FALSE;
}
