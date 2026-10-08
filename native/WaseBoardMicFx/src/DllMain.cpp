// Point d'entrée COM de la DLL : une seule classe (CWaseBoardMicFx). L'inscription (CLSID,
// AudioEngine\AudioProcessingObjects, FxProperties du micro) est faite par WaseBoard lui-même
// (MicFxSetup.cs), pas par DllRegisterServer : une seule source de vérité.

#include "WaseBoardMicFx.h"

#include <new>

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
            return E_NOINTERFACE;
        }

        // Objet statique : durée de vie liée à la DLL.
        STDMETHODIMP_(ULONG) AddRef() override { return 2; }
        STDMETHODIMP_(ULONG) Release() override { return 1; }

        STDMETHODIMP CreateInstance(IUnknown* pUnkOuter, REFIID riid, void** ppv) override
        {
            if (ppv == nullptr) return E_POINTER;
            *ppv = nullptr;
            if (pUnkOuter != nullptr) return CLASS_E_NOAGGREGATION;

            auto* apo = new (std::nothrow) CWaseBoardMicFx();
            if (apo == nullptr) return E_OUTOFMEMORY;
            const HRESULT hr = apo->QueryInterface(riid, ppv);
            apo->Release();
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
    if (reason == DLL_PROCESS_ATTACH) DisableThreadLibraryCalls(hInstance);
    return TRUE;
}

_Check_return_
STDAPI DllGetClassObject(_In_ REFCLSID rclsid, _In_ REFIID riid, _Outptr_ LPVOID FAR* ppv)
{
    if (ppv == nullptr) return E_POINTER;
    *ppv = nullptr;
    if (rclsid != CLSID_WaseBoardMicFx) return CLASS_E_CLASSNOTAVAILABLE;
    return g_classFactory.QueryInterface(riid, ppv);
}

__control_entrypoint(DllExport)
STDAPI DllCanUnloadNow(void)
{
    return (g_dllObjectCount == 0 && g_serverLocks == 0) ? S_OK : S_FALSE;
}
