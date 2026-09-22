using System;
using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Text.Json.Serialization;

namespace WaseBoard.Models
{
    /// <summary>Un utilisateur Discord actuellement en train de déclencher un son (pour l'affichage partagé), ou présent dans un salon vocal.</summary>
    public class UserActivity : INotifyPropertyChanged
    {
        public string UserId { get; set; } = "";
        public string Username { get; set; } = "";
        public string AvatarUrl { get; set; } = "";

        private bool _isActive;

        /// <summary>Vrai si cette personne est en train de jouer un son en ce moment (highlight de son avatar dans la barre latérale).</summary>
        [JsonIgnore]
        public bool IsActive
        {
            get => _isActive;
            set { if (_isActive != value) { _isActive = value; PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(IsActive))); } }
        }

        public event PropertyChangedEventHandler? PropertyChanged;
    }

    /// <summary>
    /// Représente un son du catalogue partagé, hébergé sur le serveur. Le fichier audio
    /// lui-même n'existe pas forcément localement : il est téléchargé à la demande dans
    /// un cache local pour l'aperçu.
    /// </summary>
    public class SoundItem : INotifyPropertyChanged
    {
        public string Id { get; set; } = Guid.NewGuid().ToString("N");

        /// <summary>Nom affiché sur le bouton.</summary>
        public string Name { get; set; } = "Nouveau son";

        /// <summary>Emoji affiché à côté du nom (préférence locale, purement décorative).</summary>
        public string? Emoji { get; set; }

        /// <summary>Raccourci clavier optionnel, ex: "Ctrl+Alt+1" (préférence locale).</summary>
        public string? Hotkey { get; set; }

        /// <summary>Volume individuel du son (0.0 à 1.5).</summary>
        public float Volume { get; set; } = 1.0f;

        /// <summary>Extension du fichier d'origine (ex: ".wav", ".mp3"), pour nommer correctement le cache local.</summary>
        [JsonIgnore]
        public string Extension { get; set; } = ".wav";

        /// <summary>SHA-256 du contenu, calculé côté serveur à l'upload (voir SoundLibraryService.ComputeFileHash
        /// pour la détection de doublons). Null pour les sons uploadés avant l'ajout de cette fonctionnalité.</summary>
        [JsonIgnore]
        public string? ContentHash { get; set; }

        private bool _isPlaying;

        /// <summary>
        /// État partagé (non persisté) : quelqu'un — vous ou un autre utilisateur — a déclenché ce
        /// son côté Discord dans les dernières secondes. Piloté par le sondage régulier de
        /// GET /activity, pas par la lecture locale. Utilisé pour l'effet lumineux du bouton,
        /// visible par tout le monde.
        /// </summary>
        [JsonIgnore]
        public bool IsPlaying
        {
            get => _isPlaying;
            set { if (_isPlaying != value) { _isPlaying = value; OnChanged(nameof(IsPlaying)); } }
        }

        private bool _isPreviewing;

        /// <summary>État transitoire local : un aperçu (icône mégaphone) est en cours de lecture sur vos enceintes.</summary>
        [JsonIgnore]
        public bool IsPreviewing
        {
            get => _isPreviewing;
            set { if (_isPreviewing != value) { _isPreviewing = value; OnChanged(nameof(IsPreviewing)); } }
        }

        private bool _isFavorite;

        /// <summary>Marqué en favori par l'utilisateur (préférence locale, calculée depuis AppSettings.FavoriteSoundIds).</summary>
        [JsonIgnore]
        public bool IsFavorite
        {
            get => _isFavorite;
            set { if (_isFavorite != value) { _isFavorite = value; OnChanged(nameof(IsFavorite)); } }
        }

        /// <summary>Utilisateurs actuellement en train de jouer ce son (avatars affichés sur le bouton), mis à jour par le sondage d'activité.</summary>
        [JsonIgnore]
        public ObservableCollection<UserActivity> ActiveUsers { get; } = new();

        private float[]? _waveformPeaks;

        /// <summary>Mini-waveform (peu de buckets) affichée directement sur le bouton (thème Classique uniquement).
        /// Calculée en arrière-plan et mise en cache disque, voir AudioTrimService.GetOrComputeMiniWaveform.</summary>
        [JsonIgnore]
        public float[]? WaveformPeaks
        {
            get => _waveformPeaks;
            set { if (_waveformPeaks != value) { _waveformPeaks = value; OnChanged(nameof(WaveformPeaks)); } }
        }

        public event PropertyChangedEventHandler? PropertyChanged;
        private void OnChanged(string name) => PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
    }
}
