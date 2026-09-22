using System.Collections.Generic;

namespace WaseBoard.Models
{
    public class AppSettings
    {
        /// <summary>Adresse du serveur WaseBoard (catalogue de sons + bot Discord).</summary>
        public string ServerUrl { get; set; } = "http://VOTRE_IP:5005";

        /// <summary>Jeton d'accès partagé avec le serveur (doit correspondre à shared_secret côté serveur).</summary>
        public string? ServerToken { get; set; }

        /// <summary>Identité Discord de l'utilisateur (choisie une fois dans les Paramètres), pour le highlight/avatar partagés et la résolution automatique du salon Discord ciblé.</summary>
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

        /// <summary>Emoji associé à chaque son (ID → emoji), préférence locale, purement décorative.</summary>
        public Dictionary<string, string> SoundEmojis { get; set; } = new();

        /// <summary>Ordre d'affichage personnalisé des sons dans la grille principale (liste d'IDs). Vide = ordre du serveur.</summary>
        public List<string> SoundOrder { get; set; } = new();

        /// <summary>Catégories personnalisées (nom → liste d'IDs de sons qu'elle contient), préférence locale.</summary>
        public Dictionary<string, List<string>> Categories { get; set; } = new();

        /// <summary>Ordre d'affichage personnalisé des catégories (liste de noms).</summary>
        public List<string> CategoryOrder { get; set; } = new();

        /// <summary>Clés des sections repliées sur la page principale.</summary>
        public List<string> CollapsedSections { get; set; } = new();

        /// <summary>Couleur de fond personnalisée de l'application (hex, ex: "#1E1E2E"). Null = thème par défaut.</summary>
        public string? BackgroundColorHex { get; set; }

        /// <summary>Thème d'interface : "Classic" (barre d'outils classique) ou "Modern" (barre latérale, boutons en pilule).</summary>
        public string UiTheme { get; set; } = "Modern";

        /// <summary>Si vrai, la palette clair/sombre suit automatiquement le thème Windows (ignore BackgroundColorHex).</summary>
        public bool FollowSystemTheme { get; set; } = true;

        /// <summary>Si vrai, la couleur d'accent suit automatiquement la couleur d'accent Windows.</summary>
        public bool FollowSystemAccent { get; set; } = true;

        /// <summary>Mode d'affichage des sons : "Grid" (défaut) ou "List" (ligne compacte).</summary>
        public string SoundViewMode { get; set; } = "Grid";

        /// <summary>Vrai dès que l'assistant de premier lancement a été fermé une fois (Terminer ou Passer) —
        /// évite de le rouvrir à chaque démarrage tant que le jeton/l'ID Discord ne sont pas remplis.</summary>
        public bool HasSeenOnboarding { get; set; }
    }
}
