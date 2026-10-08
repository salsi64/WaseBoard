// Protocole de la mémoire partagée entre WaseBoard.exe (producteur) et l'APO WaseBoardMicFx
// (lecteurs, chargés dans audiodg.exe). Toute modification ici doit être répercutée dans
// src/WaseBoard/Services/MicFx/MicFeedRing.cs, et faire monter WBMF_VERSION.
//
// Principe : une ligne de temps audio à 48 kHz mono float32, écrite en continu par WaseBoard
// (sons du soundboard déjà mixés), et lue par chaque instance de l'APO à son propre rythme
// (horloge du micro). writePos est le nombre total de frames écrites depuis la création :
// il ne fait qu'augmenter, l'anneau (capacity frames) n'en garde que la fin.
//
// - Un seul écrivain (WaseBoard). Plusieurs lecteurs possibles (un par micro équipé) : chacun
//   garde son propre curseur, rien n'est « consommé » dans la mémoire partagée.
// - C'est l'APO qui crée l'objet (audiodg tourne en session 0, où l'espace Global\ est l'espace
//   local) : un processus utilisateur standard n'a pas le droit d'y créer un mapping, seulement
//   d'ouvrir un objet existant.
// - L'écrivain garde ~WBMF_LEAD_FRAMES d'avance sur l'horloge murale ; un lecteur qui rejoint
//   en cours de route démarre à writePos - WBMF_LEAD_FRAMES.

#pragma once

#include <stdint.h>

#define WBMF_MAPPING_NAME   L"Global\\WaseBoardMicFeed"
#define WBMF_MAGIC          0x464D4257u   // "WBMF" en little-endian
#define WBMF_VERSION        1u
#define WBMF_SAMPLE_RATE    48000u
#define WBMF_CAPACITY       65536u        // frames, puissance de 2 (~1,37 s à 48 kHz)
#define WBMF_LEAD_FRAMES    2880u         // 60 ms : avance visée par l'écrivain
#define WBMF_MAX_LAG_FRAMES 14400u        // 300 ms : au-delà, le lecteur se recale
#define WBMF_HEADER_SIZE    64u

#pragma pack(push, 8)
typedef struct WbmfHeader
{
    uint32_t magic;            //  0 : WBMF_MAGIC une fois l'en-tête initialisé (par l'APO créateur)
    uint32_t version;          //  4 : WBMF_VERSION
    uint32_t sampleRate;       //  8 : WBMF_SAMPLE_RATE
    uint32_t capacity;         // 12 : WBMF_CAPACITY
    volatile int64_t writePos; // 16 : frames écrites au total (écrivain, publication « release »)
    volatile int64_t readTick; // 24 : GetTickCount64() du dernier APOProcess (n'importe quel lecteur)
    volatile uint32_t readerRate;     // 32 : fréquence du dernier lecteur actif (diagnostic)
    volatile uint32_t readerChannels; // 36 : nombre de canaux du dernier lecteur actif (diagnostic)
    volatile uint32_t writerPid;      // 40 : PID de l'écrivain actuel (0 = aucun)
    uint32_t reserved0;               // 44
    volatile int64_t writerTick;      // 48 : GetTickCount64() du dernier passage de l'écrivain
    uint32_t reserved1[2];            // 56
} WbmfHeader;                         // 64 octets, suivis de float samples[WBMF_CAPACITY]
#pragma pack(pop)

#ifdef __cplusplus
static_assert(sizeof(WbmfHeader) == WBMF_HEADER_SIZE, "en-tete WBMF : 64 octets attendus");
static_assert((WBMF_CAPACITY & (WBMF_CAPACITY - 1)) == 0, "WBMF_CAPACITY doit etre une puissance de 2");
#endif

#define WBMF_TOTAL_SIZE (WBMF_HEADER_SIZE + WBMF_CAPACITY * sizeof(float))
