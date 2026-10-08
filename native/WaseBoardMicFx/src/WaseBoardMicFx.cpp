#include "WaseBoardMicFx.h"

#include <audiomediatype.h>
#include <sddl.h>
#include <math.h>
#include <new>

// {8C9DCFA9-29AB-4056-936F-6721FBD44AEC}
const CLSID CLSID_WaseBoardMicFx =
    { 0x8c9dcfa9, 0x29ab, 0x4056, { 0x93, 0x6f, 0x67, 0x21, 0xfb, 0xd4, 0x4a, 0xec } };

volatile LONG g_dllObjectCount = 0;

namespace
{
    // KSDATAFORMAT_SUBTYPE_IEEE_FLOAT, recopié pour ne dépendre ni de ksmedia.h ni d'une lib de GUID.
    const GUID kSubtypeIeeeFloat =
        { 0x00000003, 0x0000, 0x0010, { 0x80, 0x00, 0x00, 0xaa, 0x00, 0x38, 0x9b, 0x71 } };

    constexpr DWORD kMaxChannels = 64;
    constexpr float kMinRate = 8000.0f;
    constexpr float kMaxRate = 384000.0f;

    bool IsSupportedFloat(const UNCOMPRESSEDAUDIOFORMAT& f)
    {
        return IsEqualGUID(f.guidFormatType, kSubtypeIeeeFloat)
            && f.dwBytesPerSampleContainer == 4
            && f.dwValidBitsPerSample == 32
            && f.dwSamplesPerFrame >= 1 && f.dwSamplesPerFrame <= kMaxChannels
            && f.fFramesPerSecond >= kMinRate && f.fFramesPerSecond <= kMaxRate;
    }

    // Écrêtage doux : transparent jusqu'à 0,85, puis tend vers 1,0 sans jamais le dépasser.
    // Évite la saturation brutale quand on parle fort pendant un son.
    inline float SoftClip(float x)
    {
        const float knee = 0.85f;
        const float ax = fabsf(x);
        if (ax <= knee) return x;
        const float over = (ax - knee) / (1.0f - knee);
        const float y = knee + (1.0f - knee) * (over / (1.0f + over));
        return x < 0.0f ? -y : y;
    }

    APO_REG_PROPERTIES MakeRegProperties()
    {
        APO_REG_PROPERTIES p = {};
        p.clsid = CLSID_WaseBoardMicFx;
        p.Flags = APO_FLAG_DEFAULT;
        wcscpy_s(p.szFriendlyName, L"WaseBoard Mic Effect");
        wcscpy_s(p.szCopyrightInfo, L"WaseBoard (licence MIT)");
        p.u32MajorVersion = 1;
        p.u32MinorVersion = 0;
        p.u32MinInputConnections = 1;
        p.u32MaxInputConnections = 1;
        p.u32MinOutputConnections = 1;
        p.u32MaxOutputConnections = 1;
        p.u32MaxInstances = 0xffffffff;
        p.u32NumAPOInterfaces = 1;
        p.iidAPOInterfaceList[0] = __uuidof(IAudioProcessingObject);
        return p;
    }
}

CWaseBoardMicFx::CWaseBoardMicFx()
{
    InterlockedIncrement(&g_dllObjectCount);
}

CWaseBoardMicFx::~CWaseBoardMicFx()
{
    CloseFeed();
    InterlockedDecrement(&g_dllObjectCount);
}

// ---------- IUnknown ----------

STDMETHODIMP CWaseBoardMicFx::QueryInterface(REFIID riid, void** ppv)
{
    if (ppv == nullptr) return E_POINTER;
    *ppv = nullptr;

    if (riid == __uuidof(IUnknown) || riid == __uuidof(IAudioProcessingObject))
        *ppv = static_cast<IAudioProcessingObject*>(this);
    else if (riid == __uuidof(IAudioProcessingObjectRT))
        *ppv = static_cast<IAudioProcessingObjectRT*>(this);
    else if (riid == __uuidof(IAudioProcessingObjectConfiguration))
        *ppv = static_cast<IAudioProcessingObjectConfiguration*>(this);
    else if (riid == __uuidof(IAudioSystemEffects))
        *ppv = static_cast<IAudioSystemEffects*>(static_cast<IAudioSystemEffects2*>(this));
    else if (riid == __uuidof(IAudioSystemEffects2))
        *ppv = static_cast<IAudioSystemEffects2*>(this);
    else
        return E_NOINTERFACE;

    AddRef();
    return S_OK;
}

STDMETHODIMP_(ULONG) CWaseBoardMicFx::AddRef()
{
    return static_cast<ULONG>(InterlockedIncrement(&m_refCount));
}

STDMETHODIMP_(ULONG) CWaseBoardMicFx::Release()
{
    const LONG count = InterlockedDecrement(&m_refCount);
    if (count == 0) delete this;
    return static_cast<ULONG>(count);
}

// ---------- IAudioProcessingObject ----------

STDMETHODIMP CWaseBoardMicFx::Reset()
{
    m_synced = false;
    return S_OK;
}

STDMETHODIMP CWaseBoardMicFx::GetLatency(HNSTIME* pTime)
{
    if (pTime == nullptr) return E_POINTER;
    *pTime = 0;
    return S_OK;
}

STDMETHODIMP CWaseBoardMicFx::GetRegistrationProperties(APO_REG_PROPERTIES** ppRegProps)
{
    if (ppRegProps == nullptr) return E_POINTER;
    *ppRegProps = static_cast<APO_REG_PROPERTIES*>(CoTaskMemAlloc(sizeof(APO_REG_PROPERTIES)));
    if (*ppRegProps == nullptr) return E_OUTOFMEMORY;
    **ppRegProps = MakeRegProperties();
    return S_OK;
}

STDMETHODIMP CWaseBoardMicFx::Initialize(UINT32 cbDataSize, BYTE* pbyData)
{
    if ((pbyData == nullptr) != (cbDataSize == 0)) return E_INVALIDARG;
    if (m_initialized) return APOERR_ALREADY_INITIALIZED;

    // Rien à lire dans la structure d'init (APOInitSystemEffects, 2 ou 3 selon Windows) :
    // l'effet ne dépend ni du mode de traitement, ni du périphérique.
    m_initialized = true;
    return S_OK;
}

HRESULT CWaseBoardMicFx::CheckFormat(IAudioMediaType* pOppositeFormat, IAudioMediaType* pRequestedFormat,
    IAudioMediaType** ppSupportedFormat)
{
    if (pRequestedFormat == nullptr || ppSupportedFormat == nullptr) return E_POINTER;
    *ppSupportedFormat = nullptr;

    UNCOMPRESSEDAUDIOFORMAT requested = {};
    if (FAILED(pRequestedFormat->GetUncompressedAudioFormat(&requested)))
        return APOERR_FORMAT_NOT_SUPPORTED;

    // Traitement « sur place » : entrée et sortie doivent avoir le même format. Si l'autre côté
    // est déjà fixé et diffère, on le propose plutôt que de refuser.
    if (pOppositeFormat != nullptr)
    {
        UNCOMPRESSEDAUDIOFORMAT opposite = {};
        if (SUCCEEDED(pOppositeFormat->GetUncompressedAudioFormat(&opposite)) && IsSupportedFloat(opposite)
            && (opposite.dwSamplesPerFrame != requested.dwSamplesPerFrame
                || opposite.fFramesPerSecond != requested.fFramesPerSecond
                || !IsSupportedFloat(requested)))
        {
            pOppositeFormat->AddRef();
            *ppSupportedFormat = pOppositeFormat;
            return S_FALSE;
        }
    }

    if (IsSupportedFloat(requested))
    {
        pRequestedFormat->AddRef();
        *ppSupportedFormat = pRequestedFormat;
        return S_OK;
    }

    // Le moteur audio propose normalement toujours du float32 aux effets système ; sinon on
    // suggère la même chose en float32.
    UNCOMPRESSEDAUDIOFORMAT asFloat = requested;
    asFloat.guidFormatType = kSubtypeIeeeFloat;
    asFloat.dwBytesPerSampleContainer = 4;
    asFloat.dwValidBitsPerSample = 32;
    if (!IsSupportedFloat(asFloat)) return APOERR_FORMAT_NOT_SUPPORTED;

    if (FAILED(CreateAudioMediaTypeFromUncompressedAudioFormat(&asFloat, ppSupportedFormat)))
        return APOERR_FORMAT_NOT_SUPPORTED;
    return S_FALSE;
}

STDMETHODIMP CWaseBoardMicFx::IsInputFormatSupported(IAudioMediaType* pOppositeFormat,
    IAudioMediaType* pRequestedInputFormat, IAudioMediaType** ppSupportedInputFormat)
{
    return CheckFormat(pOppositeFormat, pRequestedInputFormat, ppSupportedInputFormat);
}

STDMETHODIMP CWaseBoardMicFx::IsOutputFormatSupported(IAudioMediaType* pOppositeFormat,
    IAudioMediaType* pRequestedOutputFormat, IAudioMediaType** ppSupportedOutputFormat)
{
    return CheckFormat(pOppositeFormat, pRequestedOutputFormat, ppSupportedOutputFormat);
}

STDMETHODIMP CWaseBoardMicFx::GetInputChannelCount(UINT32* pu32ChannelCount)
{
    if (pu32ChannelCount == nullptr) return E_POINTER;
    *pu32ChannelCount = m_channels;
    return S_OK;
}

// ---------- IAudioProcessingObjectConfiguration ----------

STDMETHODIMP CWaseBoardMicFx::LockForProcess(UINT32 u32NumInputConnections,
    APO_CONNECTION_DESCRIPTOR** ppInputConnections, UINT32 u32NumOutputConnections,
    APO_CONNECTION_DESCRIPTOR** ppOutputConnections)
{
    if (!m_initialized) return APOERR_NOT_INITIALIZED;
    if (m_locked) return APOERR_APO_LOCKED;
    if (u32NumInputConnections != 1 || u32NumOutputConnections != 1
        || ppInputConnections == nullptr || ppOutputConnections == nullptr
        || ppInputConnections[0] == nullptr || ppOutputConnections[0] == nullptr
        || ppInputConnections[0]->pFormat == nullptr || ppOutputConnections[0]->pFormat == nullptr)
        return APOERR_NUM_CONNECTIONS_INVALID;

    UNCOMPRESSEDAUDIOFORMAT in = {}, out = {};
    HRESULT hr = ppInputConnections[0]->pFormat->GetUncompressedAudioFormat(&in);
    if (FAILED(hr)) return hr;
    hr = ppOutputConnections[0]->pFormat->GetUncompressedAudioFormat(&out);
    if (FAILED(hr)) return hr;

    if (!IsSupportedFloat(in) || !IsSupportedFloat(out)
        || in.dwSamplesPerFrame != out.dwSamplesPerFrame || in.fFramesPerSecond != out.fFramesPerSecond)
        return APOERR_INVALID_CONNECTION_FORMAT;

    m_channels = in.dwSamplesPerFrame;
    m_step = static_cast<double>(WBMF_SAMPLE_RATE) / static_cast<double>(in.fFramesPerSecond);
    m_synced = false;

    // Un échec ici n'empêche pas le micro de fonctionner : l'effet reste transparent.
    OpenFeed();

    m_locked = true;
    return S_OK;
}

STDMETHODIMP CWaseBoardMicFx::UnlockForProcess()
{
    if (!m_locked) return APOERR_ALREADY_UNLOCKED;
    m_locked = false;
    return S_OK;
}

// ---------- IAudioSystemEffects2 ----------

STDMETHODIMP CWaseBoardMicFx::GetEffectsList(LPGUID* ppEffectsIds, UINT* pcEffects, HANDLE Event)
{
    UNREFERENCED_PARAMETER(Event);
    if (ppEffectsIds == nullptr || pcEffects == nullptr) return E_POINTER;
    *ppEffectsIds = nullptr;
    *pcEffects = 0;
    return S_OK;
}

// ---------- Mémoire partagée ----------

void CWaseBoardMicFx::OpenFeed()
{
    if (m_header != nullptr) return;

    // SYSTEM / LOCAL SERVICE (audiodg) / Administrateurs : tout. Utilisateurs authentifiés :
    // lecture-écriture (WaseBoard tourne sans élévation). Étiquette d'intégrité moyenne : un
    // processus « bac à sable » (intégrité basse) ne peut pas écrire dans le micro.
    static const wchar_t* const kSddlCandidates[] = {
        L"D:(A;;GA;;;SY)(A;;GA;;;LS)(A;;GA;;;BA)(A;;GRGW;;;AU)S:(ML;;NW;;;ME)",
        L"D:(A;;GA;;;SY)(A;;GA;;;LS)(A;;GA;;;BA)(A;;GRGW;;;AU)",
    };

    HANDLE mapping = nullptr;
    bool createdHere = false;
    for (const wchar_t* sddl : kSddlCandidates)
    {
        PSECURITY_DESCRIPTOR sd = nullptr;
        if (!ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, SDDL_REVISION_1, &sd, nullptr))
            continue;
        SECURITY_ATTRIBUTES sa = { sizeof(sa), sd, FALSE };
        mapping = CreateFileMappingW(INVALID_HANDLE_VALUE, &sa, PAGE_READWRITE, 0,
            static_cast<DWORD>(WBMF_TOTAL_SIZE), WBMF_MAPPING_NAME);
        const DWORD err = GetLastError();
        LocalFree(sd);
        if (mapping != nullptr)
        {
            createdHere = (err != ERROR_ALREADY_EXISTS);
            break;
        }
    }

    if (mapping == nullptr)
        mapping = OpenFileMappingW(FILE_MAP_READ | FILE_MAP_WRITE, FALSE, WBMF_MAPPING_NAME);
    if (mapping == nullptr) return;

    void* view = MapViewOfFile(mapping, FILE_MAP_READ | FILE_MAP_WRITE, 0, 0, WBMF_TOTAL_SIZE);
    if (view == nullptr)
    {
        CloseHandle(mapping);
        return;
    }

    auto* header = static_cast<WbmfHeader*>(view);
    if (createdHere)
    {
        // Le contenu d'un mapping neuf est déjà à zéro : on remplit l'en-tête, magic en dernier.
        header->version = WBMF_VERSION;
        header->sampleRate = WBMF_SAMPLE_RATE;
        header->capacity = WBMF_CAPACITY;
        MemoryBarrier();
        header->magic = WBMF_MAGIC;
    }

    // Évite un défaut de page dans le thread temps réel (au mieux : sans effet si refusé).
    VirtualLock(view, WBMF_TOTAL_SIZE);

    m_mapping = mapping;
    m_header = header;
    m_samples = reinterpret_cast<const float*>(static_cast<BYTE*>(view) + WBMF_HEADER_SIZE);
}

void CWaseBoardMicFx::CloseFeed()
{
    if (m_header != nullptr)
    {
        UnmapViewOfFile(m_header);
        m_header = nullptr;
        m_samples = nullptr;
    }
    if (m_mapping != nullptr)
    {
        CloseHandle(m_mapping);
        m_mapping = nullptr;
    }
}

// ---------- Temps réel ----------

bool CWaseBoardMicFx::FeedHasData()
{
    WbmfHeader* const h = m_header;
    if (h == nullptr || h->magic != WBMF_MAGIC || h->version != WBMF_VERSION
        || h->capacity != WBMF_CAPACITY || h->sampleRate != WBMF_SAMPLE_RATE)
        return false;

    // Diagnostic pour WaseBoard : « un programme écoute le micro et l'effet est bien chargé ».
    h->readTick = static_cast<int64_t>(GetTickCount64());
    h->readerRate = static_cast<uint32_t>(WBMF_SAMPLE_RATE / m_step + 0.5);
    h->readerChannels = m_channels;

    const int64_t writePos = ReadAcquire64(reinterpret_cast<volatile LONG64*>(&h->writePos));
    m_writePosSnapshot = writePos;

    const double restart = static_cast<double>(writePos > WBMF_LEAD_FRAMES ? writePos - WBMF_LEAD_FRAMES : 0);
    if (!m_synced)
    {
        m_readPos = restart;
        m_synced = true;
    }

    // Retard anormal (écrivain qui a sauté, anneau réinitialisé, longue pause) : on se recale.
    const double lag = static_cast<double>(writePos) - m_readPos;
    if (lag < 0.0 || lag > static_cast<double>(WBMF_MAX_LAG_FRAMES))
        m_readPos = restart;

    return static_cast<double>(writePos) - m_readPos >= 2.0;
}

void CWaseBoardMicFx::MixFeed(float* frames, UINT32 frameCount)
{
    const int64_t writePos = m_writePosSnapshot;
    const int64_t mask = WBMF_CAPACITY - 1;
    const UINT32 channels = m_channels;

    for (UINT32 f = 0; f < frameCount; f++)
    {
        const int64_t i = static_cast<int64_t>(m_readPos);
        if (i + 1 >= writePos) break; // plus rien d'écrit : la suite du bloc reste le micro seul

        // Interpolation linéaire quand le micro ne tourne pas à 48 kHz (m_step != 1).
        const float frac = static_cast<float>(m_readPos - static_cast<double>(i));
        const float s0 = m_samples[i & mask];
        const float s1 = m_samples[(i + 1) & mask];
        const float sample = s0 + (s1 - s0) * frac;

        float* frame = frames + static_cast<size_t>(f) * channels;
        for (UINT32 c = 0; c < channels; c++)
            frame[c] = SoftClip(frame[c] + sample);

        m_readPos += m_step;
    }
}

STDMETHODIMP_(void) CWaseBoardMicFx::APOProcess(UINT32 u32NumInputConnections,
    APO_CONNECTION_PROPERTY** ppInputConnections, UINT32 u32NumOutputConnections,
    APO_CONNECTION_PROPERTY** ppOutputConnections)
{
    if (u32NumInputConnections == 0 || u32NumOutputConnections == 0) return;

    APO_CONNECTION_PROPERTY* const in = ppInputConnections[0];
    APO_CONNECTION_PROPERTY* const out = ppOutputConnections[0];
    const UINT32 frameCount = in->u32ValidFrameCount;
    const APO_BUFFER_FLAGS flags = in->u32BufferFlags;
    float* const src = reinterpret_cast<float*>(in->pBuffer);
    float* const dst = reinterpret_cast<float*>(out->pBuffer);
    const size_t bytes = static_cast<size_t>(frameCount) * m_channels * sizeof(float);

    if (flags == BUFFER_INVALID || m_channels == 0)
    {
        out->u32ValidFrameCount = frameCount;
        out->u32BufferFlags = flags;
        return;
    }

    const bool silent = (flags == BUFFER_SILENT);
    if (!silent && dst != src)
        CopyMemory(dst, src, bytes);

    if (m_locked && FeedHasData())
    {
        // Un tampon « silencieux » n'a pas de contenu garanti : on part de vrais zéros.
        if (silent) ZeroMemory(dst, bytes);
        MixFeed(dst, frameCount);
        out->u32BufferFlags = BUFFER_VALID;
    }
    else
    {
        out->u32BufferFlags = flags;
    }
    out->u32ValidFrameCount = frameCount;
}

STDMETHODIMP_(UINT32) CWaseBoardMicFx::CalcInputFrames(UINT32 u32OutputFrameCount)
{
    return u32OutputFrameCount;
}

STDMETHODIMP_(UINT32) CWaseBoardMicFx::CalcOutputFrames(UINT32 u32InputFrameCount)
{
    return u32InputFrameCount;
}

