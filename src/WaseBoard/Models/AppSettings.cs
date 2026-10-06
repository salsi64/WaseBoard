using System.Collections.Generic;
using System.Text.Json.Serialization;

namespace WaseBoard.Models
{
    public class AppSettings
    {
        /// <summary>Adresse du serveur WaseBoard (catalogue de sons + bot Discord). Vide par défaut :
        /// chaque installation cible son propre serveur (voir server/README.md pour l'héberger),
        /// aucune adresse par défaut n'est fournie avec l'application.</summary>
        public string ServerUrl { get; set; } = "";

        /// <summary>Jeton d'accès partagé avec le serveur (doit correspondre à shared_secret côté
        /// serveur). En mémoire uniquement — la valeur persistée (chiffrée) est EncryptedServerToken,
        /// voir SoundLibraryService.Load()/SaveSettings().</summary>
        [JsonIgnore]
        public string? ServerToken { get; set; }

        /// <summary>Forme chiffrée (DPAPI) de ServerToken, seule persistée dans settings.json.</summary>
        public string? EncryptedServerToken { get; set; }

        /// <summary>Ancien champ en clair (avant chiffrement) — lu une seule fois pour migrer les
        /// installations existantes vers EncryptedServerToken, jamais réécrit après.</summary>
        [JsonPropertyName("ServerToken")]
        public string? LegacyServerTokenPlaintext { get; set; }

        /// <summary>Jeton de session WaseBoard (identité Discord vérifiée par OAuth2). En mémoire
        /// uniquement — la valeur persistée (chiffrée) est EncryptedDiscordSessionToken.</summary>
        [JsonIgnore]
        public string? DiscordSessionToken { get; set; }

        /// <summary>Forme chiffrée (DPAPI) de DiscordSessionToken, seule persistée dans settings.json.</summary>
        public string? EncryptedDiscordSessionToken { get; set; }

        /// <summary>Identité Discord affichée (pseudo/avatar), renseignée par la connexion OAuth2 —
        /// purement informatif, plus jamais envoyé au serveur (l'identité réelle vient du jeton de
        /// session ci-dessus).</summary>
        public string? DiscordUserId { get; set; }
        public string? DiscordUsername { get; set; }
        public string? DiscordAvatarUrl { get; set; }

        /// <summary>Volume de la lecture locale (aperçu uniquement).</summary>
        public float LocalPlaybackVolume { get; set; } = 1.0f;

        /// <summary>IDs des sons marqués en favoris (préférence locale, propre à cet utilisateur).</summary>
        public List<string> FavoriteSoundIds { get; set; } = new();

        /// <summary>Raccourcis clavier par son (ID → raccourci, ex: "Ctrl+Alt+1"), préférence locale.</summary>
        public Dictionary<string, string> SoundHotkeys { get; set; } = new();

        /// <summary>Volume individuel par son (ID → volume 0.0-1.5), préférence locale.</summary>
        public Dictionary<string, float> SoundVolumes { get; set; } = new();

        /// <summary>Couleur choisie par son (ID → "#RRGGBB"), préférence locale. Absent = couleur automatique dérivée de l'ID.</summary>
        public Dictionary<string, string> SoundColors { get; set; } = new();

        /// <summary>Ordre d'affichage personnalisé des sons dans la grille principale (liste d'IDs). Vide = ordre du serveur.</summary>
        public List<string> SoundOrder { get; set; } = new();

        /// <summary>Catégories personnalisées (nom → liste d'IDs de sons qu'elle contient), préférence locale.</summary>
        public Dictionary<string, List<string>> Categories { get; set; } = new();

        /// <summary>Ordre d'affichage personnalisé des catégories (liste de noms).</summary>
        public List<string> CategoryOrder { get; set; } = new();

        /// <summary>Clés des sections repliées sur la page principale.</summary>
        public List<string> CollapsedSections { get; set; } = new();

        /// <summary>Tri appliqué à chaque section de guilde : "Custom" (ordre d'affichage actuel,
        /// glisser-déposer manuel), "NameAsc" ou "NameDesc".</summary>
        public string AllSoundsSortMode { get; set; } = "Custom";


        /// <summary>Couleur de fond personnalisée de l'application (hex, ex: "#1E1E2E"). Null = thème par défaut.</summary>
        public string? BackgroundColorHex { get; set; }

        /// <summary>Thème d'interface : "Classic" (barre d'outils classique), "Modern" (barre latérale, boutons en pilule)
        /// ou "Flat" (disposition moderne + style d'interface Flat ; indépendant de la palette de couleurs).</summary>
        public string UiTheme { get; set; } = "Modern";

        /// <summary>Si vrai, la palette clair/sombre suit automatiquement le thème Windows (ignore BackgroundColorHex).</summary>
        public bool FollowSystemTheme { get; set; } = true;

        /// <summary>Si vrai, la couleur d'accent suit automatiquement la couleur d'accent Windows
        /// (au lieu de l'accent de la palette choisie).</summary>
        public bool FollowSystemAccent { get; set; } = true;

        /// <summary>Palette de l'interface (voir PalettePresets). Null = réglages antérieurs aux palettes :
        /// migrés une fois au chargement (SoundLibraryService.MigrateAppearance).</summary>
        public string? PaletteId { get; set; }

        /// <summary>Affiche la mini-waveform sur les boutons de son. Désactivée : boutons plus compacts
        /// (une seule ligne), l'avatar de qui joue passe alors à droite de la ligne du nom.</summary>
        public bool ShowWaveforms { get; set; } = true;

        /// <summary>Vrai dès que l'assistant de premier lancement a été fermé une fois (Terminer ou Passer) —
        /// évite de le rouvrir à chaque démarrage tant que le jeton/la connexion Discord ne sont pas remplis.</summary>
        public bool HasSeenOnboarding { get; set; }
    }
}
