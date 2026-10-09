// Test d'intégration de WaseBoardMicFx.dll, sans moteur audio : ce programme joue le rôle
// d'audiodg (négociation de format, LockForProcess, blocs de micro passés à APOProcess) ET celui de
// WaseBoard (écriture des sons dans la mémoire partagée), puis vérifie le signal de sortie.
//
// Doit tourner élevé (création d'un objet Global\) : c'est le cas sur les runners GitHub Actions.
// Usage : ApoTest.exe <chemin de WaseBoardMicFx.dll>

#include <windows.h>
#include <mmdeviceapi.h>
#include <audioenginebaseapo.h>
#include <audiomediatype.h>

#include <math.h>
#include <stdio.h>
#include <vector>

#include "../src/MicFeedProtocol.h"

namespace
{
    const CLSID kClsid = { 0x8c9dcfa9, 0x29ab, 0x4056, { 0x93, 0x6f, 0x67, 0x21, 0xfb, 0xd4, 0x4a, 0xec } };
    const GUID kFloat = { 0x00000003, 0x0000, 0x0010, { 0x80, 0x00, 0x00, 0xaa, 0x00, 0x38, 0x9b, 0x71 } };
    const GUID kPcm = { 0x00000001, 0x0000, 0x0010, { 0x80, 0x00, 0x00, 0xaa, 0x00, 0x38, 0x9b, 0x71 } };

    int g_failures = 0;

#define CHECK(cond, ...)                                                  \
    do {                                                                  \
        if (!(cond)) {                                                    \
            g_failures++;                                                 \
            printf("ECHEC  %s:%d  %s\n        ", __FILE__, __LINE__, #cond); \
            printf(__VA_ARGS__);                                          \
            printf("\n");                                                 \
        }                                                                 \
    } while (0)

    bool Near(float a, float b, float tolerance = 1e-4f) { return fabsf(a - b) <= tolerance; }

    IAudioMediaType* MakeFormat(const GUID& subtype, DWORD channels, float rate, DWORD bytesPerSample)
    {
        UNCOMPRESSEDAUDIOFORMAT f = {};
        f.guidFormatType = subtype;
        f.dwSamplesPerFrame = channels;
        f.dwBytesPerSampleContainer = bytesPerSample;
        f.dwValidBitsPerSample = bytesPerSample * 8;
        f.fFramesPerSecond = rate;
        f.dwChannelMask = channels == 2 ? 0x3 /* avant gauche + droite */ : 0x4 /* centre */;
        IAudioMediaType* type = nullptr;
        if (FAILED(CreateAudioMediaTypeFromUncompressedAudioFormat(&f, &type))) return nullptr;
        return type;
    }

    // Propriétés d'endpoint minimales : seul PKEY_AudioEndpoint_GUID est lu par l'effet.
    const PROPERTYKEY kEndpointGuidKey =
        { { 0x1da5d803, 0xd492, 0x4edd, { 0x8c, 0x23, 0xe0, 0xc0, 0xff, 0xee, 0x7f, 0x0e } }, 4 };
    const GUID kTestEndpoint = { 0x11111111, 0x2222, 0x3333, { 0x44, 0x44, 0x55, 0x55, 0x55, 0x55, 0x55, 0x55 } };

    struct FakeEndpointStore : IPropertyStore
    {
        STDMETHODIMP QueryInterface(REFIID riid, void** ppv) override
        {
            if (riid == __uuidof(IUnknown) || riid == __uuidof(IPropertyStore)) { *ppv = this; return S_OK; }
            *ppv = nullptr;
            return E_NOINTERFACE;
        }
        STDMETHODIMP_(ULONG) AddRef() override { return 2; }
        STDMETHODIMP_(ULONG) Release() override { return 1; }
        STDMETHODIMP GetCount(DWORD* count) override { *count = 1; return S_OK; }
        STDMETHODIMP GetAt(DWORD, PROPERTYKEY* key) override { *key = kEndpointGuidKey; return S_OK; }
        STDMETHODIMP GetValue(REFPROPERTYKEY key, PROPVARIANT* value) override
        {
            PropVariantInit(value);
            if (key.fmtid != kEndpointGuidKey.fmtid || key.pid != kEndpointGuidKey.pid) return S_OK;
            static const wchar_t text[] = L"{11111111-2222-3333-4444-555555555555}";
            value->pwszVal = static_cast<LPWSTR>(CoTaskMemAlloc(sizeof(text)));
            memcpy(value->pwszVal, text, sizeof(text));
            value->vt = VT_LPWSTR;
            return S_OK;
        }
        STDMETHODIMP SetValue(REFPROPERTYKEY, REFPROPVARIANT) override { return E_NOTIMPL; }
        STDMETHODIMP Commit() override { return E_NOTIMPL; }
    };

    // Côté « WaseBoard » : écrit des frames 48 kHz à la suite de la ligne de temps.
    struct Writer
    {
        WbmfHeader* header = nullptr;
        float* samples = nullptr;

        void Write(const std::vector<float>& frames)
        {
            int64_t pos = header->writePos;
            for (size_t i = 0; i < frames.size(); i++)
                samples[(pos + static_cast<int64_t>(i)) & (WBMF_CAPACITY - 1)] = frames[i];
            MemoryBarrier();
            header->writePos = pos + static_cast<int64_t>(frames.size());
        }
    };

    // Côté « audiodg » : un bloc de micro passé à l'APO, sur place (même tampon en entrée/sortie).
    struct Engine
    {
        IAudioProcessingObjectRT* rt = nullptr;
        UINT32 channels = 2;
        std::vector<float> buffer;
        APO_CONNECTION_PROPERTY in = {};
        APO_CONNECTION_PROPERTY out = {};

        APO_BUFFER_FLAGS Process(UINT32 frames, float micValue, APO_BUFFER_FLAGS flags = BUFFER_VALID)
        {
            buffer.assign(static_cast<size_t>(frames) * channels, micValue);
            in = { reinterpret_cast<UINT_PTR>(buffer.data()), frames, flags, APO_CONNECTION_PROPERTY_SIGNATURE };
            out = { reinterpret_cast<UINT_PTR>(buffer.data()), 0, BUFFER_INVALID, APO_CONNECTION_PROPERTY_SIGNATURE };
            APO_CONNECTION_PROPERTY* pin = &in;
            APO_CONNECTION_PROPERTY* pout = &out;
            rt->APOProcess(1, &pin, 1, &pout);
            return out.u32BufferFlags;
        }

        float Sample(UINT32 frame, UINT32 channel = 0) const { return buffer[static_cast<size_t>(frame) * channels + channel]; }
    };

    HRESULT Lock(IAudioProcessingObjectConfiguration* config, IAudioMediaType* format, std::vector<float>& storage)
    {
        storage.assign(4800 * 2, 0.0f);
        APO_CONNECTION_DESCRIPTOR in = { APO_CONNECTION_BUFFER_TYPE_EXTERNAL, reinterpret_cast<UINT_PTR>(storage.data()), 4800, format, APO_CONNECTION_DESCRIPTOR_SIGNATURE };
        APO_CONNECTION_DESCRIPTOR out = in;
        APO_CONNECTION_DESCRIPTOR* pin = &in;
        APO_CONNECTION_DESCRIPTOR* pout = &out;
        return config->LockForProcess(1, &pin, 1, &pout);
    }
}

int wmain(int argc, wchar_t** argv)
{
    if (argc < 2)
    {
        printf("Usage : ApoTest.exe <WaseBoardMicFx.dll>\n");
        return 2;
    }

    HMODULE dll = LoadLibraryW(argv[1]);
    if (dll == nullptr)
    {
        printf("Impossible de charger %ls (erreur %lu)\n", argv[1], GetLastError());
        return 2;
    }
    using GetClassObjectFn = HRESULT(STDAPICALLTYPE*)(REFCLSID, REFIID, LPVOID*);
    using CanUnloadFn = HRESULT(STDAPICALLTYPE*)();
    auto getClassObject = reinterpret_cast<GetClassObjectFn>(GetProcAddress(dll, "DllGetClassObject"));
    auto canUnload = reinterpret_cast<CanUnloadFn>(GetProcAddress(dll, "DllCanUnloadNow"));
    CHECK(getClassObject != nullptr && canUnload != nullptr, "exports COM manquants");
    if (getClassObject == nullptr || canUnload == nullptr) return 1;

    // --- Création COM ---
    IClassFactory* factory = nullptr;
    CHECK(SUCCEEDED(getClassObject(kClsid, __uuidof(IClassFactory), reinterpret_cast<void**>(&factory))), "fabrique introuvable");
    IAudioProcessingObject* apo = nullptr;
    CHECK(SUCCEEDED(factory->CreateInstance(nullptr, __uuidof(IAudioProcessingObject), reinterpret_cast<void**>(&apo))), "CreateInstance");

    // --- Agrégation COM (le moteur audio peut agréger les APO) ---
    {
        struct Outer : IUnknown
        {
            LONG refs = 1;
            IUnknown* inner = nullptr;
            STDMETHODIMP QueryInterface(REFIID riid, void** ppv) override
            {
                if (riid == __uuidof(IUnknown)) { *ppv = static_cast<IUnknown*>(this); AddRef(); return S_OK; }
                return inner->QueryInterface(riid, ppv);
            }
            STDMETHODIMP_(ULONG) AddRef() override { return static_cast<ULONG>(++refs); }
            STDMETHODIMP_(ULONG) Release() override { return static_cast<ULONG>(--refs); }
        } outer;

        void* refused = nullptr;
        CHECK(factory->CreateInstance(&outer, __uuidof(IAudioProcessingObject), &refused) == CLASS_E_NOAGGREGATION && refused == nullptr,
            "agrégation : seul IUnknown peut être demandé");
        CHECK(SUCCEEDED(factory->CreateInstance(&outer, __uuidof(IUnknown), reinterpret_cast<void**>(&outer.inner))) && outer.inner != nullptr,
            "agrégation : CreateInstance(IUnknown)");
        if (outer.inner != nullptr)
        {
            IAudioProcessingObjectRT* aggregatedRt = nullptr;
            CHECK(SUCCEEDED(outer.QueryInterface(__uuidof(IAudioProcessingObjectRT), reinterpret_cast<void**>(&aggregatedRt))), "agrégation : QI via l'agrégateur");
            CHECK(outer.refs == 2, "agrégation : AddRef délégué à l'agrégateur (refs=%ld)", outer.refs);
            IUnknown* identity = nullptr;
            if (aggregatedRt != nullptr)
            {
                aggregatedRt->QueryInterface(__uuidof(IUnknown), reinterpret_cast<void**>(&identity));
                CHECK(identity == static_cast<IUnknown*>(&outer), "agrégation : identité COM = agrégateur");
                if (identity != nullptr) identity->Release();
                aggregatedRt->Release();
            }
            CHECK(outer.refs == 1, "agrégation : Release délégué (refs=%ld)", outer.refs);
            CHECK(outer.inner->Release() == 0, "agrégation : libération de l'objet interne");
        }
    }

    factory->Release();
    if (apo == nullptr) return 1;

    IAudioProcessingObjectRT* rt = nullptr;
    IAudioProcessingObjectConfiguration* config = nullptr;
    IAudioSystemEffects2* fx2 = nullptr;
    IAudioSystemEffects* fx = nullptr;
    CHECK(SUCCEEDED(apo->QueryInterface(__uuidof(IAudioProcessingObjectRT), reinterpret_cast<void**>(&rt))), "QI RT");
    CHECK(SUCCEEDED(apo->QueryInterface(__uuidof(IAudioProcessingObjectConfiguration), reinterpret_cast<void**>(&config))), "QI Configuration");
    CHECK(SUCCEEDED(apo->QueryInterface(__uuidof(IAudioSystemEffects2), reinterpret_cast<void**>(&fx2))), "QI SystemEffects2");
    CHECK(SUCCEEDED(apo->QueryInterface(__uuidof(IAudioSystemEffects), reinterpret_cast<void**>(&fx))), "QI SystemEffects");
    if (rt == nullptr || config == nullptr) return 1;

    // --- Enregistrement et initialisation ---
    APO_REG_PROPERTIES* reg = nullptr;
    CHECK(SUCCEEDED(apo->GetRegistrationProperties(&reg)) && reg != nullptr, "GetRegistrationProperties");
    if (reg != nullptr)
    {
        CHECK(IsEqualCLSID(reg->clsid, kClsid), "CLSID d'enregistrement");
        CHECK(reg->Flags == APO_FLAG_DEFAULT, "Flags = %d", static_cast<int>(reg->Flags));
        CHECK(reg->u32NumAPOInterfaces == 1 && reg->iidAPOInterfaceList[0] == __uuidof(IAudioProcessingObject), "interfaces déclarées");
        CoTaskMemFree(reg);
    }

    FakeEndpointStore endpointStore;
    APOInitSystemEffects2 init = {};
    init.APOInit.cbSize = sizeof(init);
    init.APOInit.clsid = kClsid;
    init.pAPOEndpointProperties = &endpointStore;
    CHECK(apo->Initialize(sizeof(init), reinterpret_cast<BYTE*>(&init)) == S_OK, "Initialize");
    CHECK(apo->Initialize(sizeof(init), reinterpret_cast<BYTE*>(&init)) == APOERR_ALREADY_INITIALIZED, "double Initialize");

    LPGUID effects = nullptr;
    UINT effectCount = 99;
    CHECK(fx2->GetEffectsList(&effects, &effectCount, nullptr) == S_OK && effects == nullptr && effectCount == 0, "liste d'effets vide");

    // --- Négociation de format ---
    IAudioMediaType* float48 = MakeFormat(kFloat, 2, 48000.0f, 4);
    IAudioMediaType* pcm48 = MakeFormat(kPcm, 2, 48000.0f, 2);
    IAudioMediaType* float44 = MakeFormat(kFloat, 2, 44100.0f, 4);
    CHECK(float48 && pcm48 && float44, "création des formats de test");
    if (!float48 || !pcm48 || !float44) return 1;

    IAudioMediaType* supported = nullptr;
    CHECK(apo->IsInputFormatSupported(nullptr, float48, &supported) == S_OK && supported == float48, "float32 accepté tel quel");
    if (supported) supported->Release();
    supported = nullptr;
    CHECK(apo->IsInputFormatSupported(nullptr, pcm48, &supported) == S_FALSE && supported != nullptr, "PCM16 -> proposition float32");
    if (supported)
    {
        UNCOMPRESSEDAUDIOFORMAT f = {};
        supported->GetUncompressedAudioFormat(&f);
        CHECK(IsEqualGUID(f.guidFormatType, kFloat) && f.dwBytesPerSampleContainer == 4 && f.dwSamplesPerFrame == 2, "format proposé");
        supported->Release();
    }

    // --- Mémoire partagée créée par LockForProcess ---
    std::vector<float> lockStorage;
    CHECK(Lock(config, float48, lockStorage) == S_OK, "LockForProcess 48 kHz");
    CHECK(Lock(config, float48, lockStorage) == APOERR_APO_LOCKED, "double LockForProcess");

    HANDLE mapping = OpenFileMappingW(FILE_MAP_READ | FILE_MAP_WRITE, FALSE, WBMF_MAPPING_NAME);
    CHECK(mapping != nullptr, "mémoire partagée %ls absente (erreur %lu) — le test doit tourner élevé", WBMF_MAPPING_NAME, GetLastError());
    if (mapping == nullptr) return 1;
    auto* view = static_cast<BYTE*>(MapViewOfFile(mapping, FILE_MAP_READ | FILE_MAP_WRITE, 0, 0, WBMF_TOTAL_SIZE));
    CHECK(view != nullptr, "MapViewOfFile");
    if (view == nullptr) return 1;

    Writer writer;
    writer.header = reinterpret_cast<WbmfHeader*>(view);
    writer.samples = reinterpret_cast<float*>(view + WBMF_HEADER_SIZE);
    CHECK(writer.header->magic == WBMF_MAGIC && writer.header->version == WBMF_VERSION
        && writer.header->capacity == WBMF_CAPACITY && writer.header->sampleRate == WBMF_SAMPLE_RATE, "en-tête initialisé par l'APO");

    Engine engine;
    engine.rt = rt;

    // 1. Rien d'écrit : le micro passe tel quel.
    CHECK(engine.Process(480, 0.1f) == BUFFER_VALID, "flags inchangés");
    CHECK(Near(engine.Sample(0), 0.1f) && Near(engine.Sample(479, 1), 0.1f), "passthrough sans son : %f", engine.Sample(0));
    CHECK(writer.header->readTick != 0, "readTick mis à jour");
    CHECK(writer.header->readerRate == 48000 && writer.header->readerChannels == 2, "diagnostic lecteur : %u Hz, %u canaux",
        writer.header->readerRate, writer.header->readerChannels);
    {
        const WbmfReaderSlot& slot = writer.header->readers[kTestEndpoint.Data1 % WBMF_READER_SLOTS];
        CHECK(IsEqualGUID(slot.endpoint, kTestEndpoint) && slot.tick != 0, "emplacement du micro horodaté (tick=%lld)", slot.tick);
    }

    // 2. Un son (0,25 constant) : sortie = micro + son, sur chaque canal.
    writer.Write(std::vector<float>(4800, 0.25f));
    engine.Process(480, 0.1f);
    CHECK(Near(engine.Sample(0), 0.35f) && Near(engine.Sample(0, 1), 0.35f) && Near(engine.Sample(479, 1), 0.35f),
        "mélange 0,1 + 0,25 : %f / %f", engine.Sample(0), engine.Sample(479, 1));

    // 3. Bloc « silencieux » au contenu indéfini : traité comme du vrai silence, marqué valide.
    engine.buffer.assign(480 * 2, 123.0f);
    {
        std::vector<float> garbage(480 * 2, 123.0f);
        engine.in = { reinterpret_cast<UINT_PTR>(garbage.data()), 480, BUFFER_SILENT, APO_CONNECTION_PROPERTY_SIGNATURE };
        engine.out = { reinterpret_cast<UINT_PTR>(garbage.data()), 0, BUFFER_INVALID, APO_CONNECTION_PROPERTY_SIGNATURE };
        APO_CONNECTION_PROPERTY* pin = &engine.in;
        APO_CONNECTION_PROPERTY* pout = &engine.out;
        rt->APOProcess(1, &pin, 1, &pout);
        CHECK(engine.out.u32BufferFlags == BUFFER_VALID, "silencieux + son -> VALID");
        CHECK(Near(garbage[0], 0.25f) && Near(garbage[959], 0.25f), "silencieux + son = son seul : %f", garbage[0]);
    }

    // 4. Écrêtage doux : jamais au-delà de 1,0, et plus de son au-delà de ce qui est écrit.
    engine.Process(480, 0.95f);
    CHECK(engine.Sample(0) < 1.0f && engine.Sample(0) > 0.95f, "écrêtage doux : %f", engine.Sample(0));

    // 4800 frames écrites, 1440 consommées : il en reste 3360. Un bloc de 4000 en épuise le reste
    // puis laisse passer le micro seul.
    engine.Process(4000, 0.1f);
    CHECK(Near(engine.Sample(100), 0.35f), "fin de son encore mélangée : %f", engine.Sample(100));
    CHECK(Near(engine.Sample(3999), 0.1f), "après la fin du son, micro seul : %f", engine.Sample(3999));

    // 5. Retard aberrant (écrivain très en avance) : le lecteur se recale à writePos - avance.
    std::vector<float> ramp(WBMF_MAX_LAG_FRAMES * 2);
    for (size_t i = 0; i < ramp.size(); i++) ramp[i] = static_cast<float>(i) / static_cast<float>(ramp.size()) * 0.5f;
    writer.Write(ramp);
    engine.Process(1, 0.0f);
    const float expected = static_cast<float>(ramp.size() - WBMF_LEAD_FRAMES) / static_cast<float>(ramp.size()) * 0.5f;
    CHECK(Near(engine.Sample(0), expected, 1e-3f), "recalage après retard : %f (attendu %f)", engine.Sample(0), expected);

    // 6. Micro à 44,1 kHz : lecture de l'anneau 48 kHz avec interpolation (pas de 48000/44100).
    CHECK(config->UnlockForProcess() == S_OK, "UnlockForProcess");
    CHECK(config->UnlockForProcess() == APOERR_ALREADY_UNLOCKED, "double UnlockForProcess");
    CHECK(apo->Reset() == S_OK, "Reset");
    CHECK(Lock(config, float44, lockStorage) == S_OK, "LockForProcess 44,1 kHz");

    // LockForProcess remet le curseur à « non synchronisé » : le premier bloc démarre à
    // writePos - avance, donc ici à l'indice (taille - avance) de la rampe linéaire écrite.
    std::vector<float> line(9600);
    for (size_t i = 0; i < line.size(); i++) line[i] = static_cast<float>(i) * 1e-5f;
    writer.Write(line);
    engine.Process(441, 0.0f);

    // Frame k du micro = position (taille - avance) + k * 48000/44100 dans la rampe (interpolée).
    const double step = 48000.0 / 44100.0;
    const double first = static_cast<double>(line.size() - WBMF_LEAD_FRAMES);
    const float expected0 = static_cast<float>(first * 1e-5);
    const float expected440 = static_cast<float>((first + 440 * step) * 1e-5);
    CHECK(Near(engine.Sample(0), expected0, 1e-5f), "44,1 kHz, frame 0 : %f (attendu %f)", engine.Sample(0), expected0);
    CHECK(Near(engine.Sample(440), expected440, 1e-5f), "44,1 kHz, frame 440 : %f (attendu %f)", engine.Sample(440), expected440);
    CHECK(writer.header->readerRate == 44100, "diagnostic 44,1 kHz : %u", writer.header->readerRate);

    // --- Fin de vie COM ---
    config->UnlockForProcess();
    UnmapViewOfFile(view);
    CloseHandle(mapping);
    float48->Release();
    pcm48->Release();
    float44->Release();
    fx->Release();
    fx2->Release();
    rt->Release();
    config->Release();
    CHECK(apo->Release() == 0, "compteur de références final");
    CHECK(canUnload() == S_OK, "DllCanUnloadNow après libération");

    if (g_failures == 0) printf("OK : tous les tests de WaseBoardMicFx passent.\n");
    else printf("%d échec(s).\n", g_failures);
    return g_failures == 0 ? 0 : 1;
}
