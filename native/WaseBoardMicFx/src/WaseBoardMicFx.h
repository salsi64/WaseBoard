// Effet audio (APO) « WaseBoard Mic » : ajoute les sons du soundboard au signal du vrai micro.
//
// Inscrit comme effet d'endpoint (EFX) en fin de liste composite du micro
// (PKEY_CompositeFX_EndpointEffectClsid) : il s'applique à tous les flux du micro, y compris en
// mode RAW, après les effets du fabricant, et ne remplace rien. Voir native/WaseBoardMicFx/README.md.
//
// Règle d'or : ne JAMAIS échouer pour une raison qui nous est propre. Windows compte les échecs
// de CoCreateInstance / IsInputFormatSupported / LockForProcess et, au 10e, désactive TOUS les
// effets du micro (PKEY_Endpoint_Disable_SysFx). Si la mémoire partagée est absente ou invalide,
// l'effet se contente de laisser passer le micro tel quel.

#pragma once

#include <windows.h>
#include <mmdeviceapi.h>
#include <audioenginebaseapo.h>

#include "MicFeedProtocol.h"

// {8C9DCFA9-29AB-4056-936F-6721FBD44AEC}
extern const CLSID CLSID_WaseBoardMicFx;

// Objets vivants + verrous serveur, pour DllCanUnloadNow.
extern volatile LONG g_dllObjectCount;

class CWaseBoardMicFx final :
    public IAudioProcessingObject,
    public IAudioProcessingObjectRT,
    public IAudioProcessingObjectConfiguration,
    public IAudioSystemEffects2
{
public:
    CWaseBoardMicFx();

    // IUnknown
    STDMETHODIMP QueryInterface(REFIID riid, void** ppv) override;
    STDMETHODIMP_(ULONG) AddRef() override;
    STDMETHODIMP_(ULONG) Release() override;

    // IAudioProcessingObject
    STDMETHODIMP Reset() override;
    STDMETHODIMP GetLatency(HNSTIME* pTime) override;
    STDMETHODIMP GetRegistrationProperties(APO_REG_PROPERTIES** ppRegProps) override;
    STDMETHODIMP Initialize(UINT32 cbDataSize, BYTE* pbyData) override;
    STDMETHODIMP IsInputFormatSupported(IAudioMediaType* pOppositeFormat,
        IAudioMediaType* pRequestedInputFormat, IAudioMediaType** ppSupportedInputFormat) override;
    STDMETHODIMP IsOutputFormatSupported(IAudioMediaType* pOppositeFormat,
        IAudioMediaType* pRequestedOutputFormat, IAudioMediaType** ppSupportedOutputFormat) override;
    STDMETHODIMP GetInputChannelCount(UINT32* pu32ChannelCount) override;

    // IAudioProcessingObjectRT (temps réel : ni blocage, ni allocation, ni appel système lent)
    STDMETHODIMP_(void) APOProcess(UINT32 u32NumInputConnections, APO_CONNECTION_PROPERTY** ppInputConnections,
        UINT32 u32NumOutputConnections, APO_CONNECTION_PROPERTY** ppOutputConnections) override;
    STDMETHODIMP_(UINT32) CalcInputFrames(UINT32 u32OutputFrameCount) override;
    STDMETHODIMP_(UINT32) CalcOutputFrames(UINT32 u32InputFrameCount) override;

    // IAudioProcessingObjectConfiguration
    STDMETHODIMP LockForProcess(UINT32 u32NumInputConnections, APO_CONNECTION_DESCRIPTOR** ppInputConnections,
        UINT32 u32NumOutputConnections, APO_CONNECTION_DESCRIPTOR** ppOutputConnections) override;
    STDMETHODIMP UnlockForProcess() override;

    // IAudioSystemEffects2 (aucun effet « standard » à déclarer : liste vide)
    STDMETHODIMP GetEffectsList(LPGUID* ppEffectsIds, UINT* pcEffects, HANDLE Event) override;

private:
    ~CWaseBoardMicFx();

    HRESULT CheckFormat(IAudioMediaType* pOppositeFormat, IAudioMediaType* pRequestedFormat,
        IAudioMediaType** ppSupportedFormat);

    // Mémoire partagée (hors temps réel)
    void OpenFeed();
    void CloseFeed();

    // Temps réel
    bool FeedHasData();
    void MixFeed(float* frames, UINT32 frameCount);

    volatile LONG m_refCount = 1;
    bool m_initialized = false;
    bool m_locked = false;

    UINT32 m_channels = 0;
    double m_step = 1.0; // frames de l'anneau (48 kHz) consommées par frame du micro

    HANDLE m_mapping = nullptr;
    WbmfHeader* m_header = nullptr;
    const float* m_samples = nullptr;

    // Curseur de lecture propre à cette instance (position dans la ligne de temps de l'anneau).
    bool m_synced = false;
    double m_readPos = 0.0;
    int64_t m_writePosSnapshot = 0;
};
