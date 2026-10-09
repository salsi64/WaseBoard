// Journal de diagnostic de l'effet : %ProgramData%\WaseBoard\MicFx\apo.log (lisible par les
// utilisateurs, écrit par audiodg.exe). Sert à savoir ce que le moteur audio fait réellement de
// l'effet (chargement, formats, démarrage, mémoire partagée), invisible autrement sans droits admin.
//
// JAMAIS depuis le chemin temps réel (APOProcess) : ouverture/écriture de fichier = appels bloquants.
// Jamais non plus depuis DllMain (verrou du chargeur).

#pragma once

#include <windows.h>

namespace DiagLog
{
    void Write(_Printf_format_string_ const wchar_t* format, ...);

    // Une seule fois par processus : qui nous charge, avec quel jeton (session, compte, intégrité,
    // AppContainer) — déterminant pour la création d'objets Global\.
    void WriteProcessInfoOnce(HMODULE self);

    // GUID au format registre, pour les traces (buffer d'au moins 39 caractères).
    const wchar_t* GuidToString(REFGUID guid, wchar_t* buffer, size_t count);

    // Vrai la première fois qu'on voit ce GUID dans ce processus : le moteur audio redemande les
    // mêmes interfaces optionnelles à chaque instance, une ligne par interface suffit.
    bool FirstTimeSeen(REFGUID guid);
}
