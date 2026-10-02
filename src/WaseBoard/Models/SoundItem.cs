using System;
using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Linq;
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

        /// <summary>Guilde Discord d'origine (celle où ce son a été uploadé) — donnée serveur, pas une préférence locale.</summary>
        public string? GuildId { get; set; }

        /// <summary>Id Discord de l'auteur de l'upload (donnée serveur) ; null pour un ancien son sans auteur enregistré.</summary>
        [JsonIgnore]
        public string? UploadedBy { get; set; }

        /// <summary>Vous êtes l'auteur de ce son. Calculé par le serveur pour VOTRE session (non persisté).</summary>
        [JsonIgnore]
        public bool IsMine { get; set; }

        /// <summary>Vous pouvez renommer/changer l'emoji/supprimer ce son (auteur ou admin de sa guilde).
        /// Sert uniquement à masquer les actions interdites : le serveur re-vérifie chaque modification.</summary>
        [JsonIgnore]
        public bool CanEdit { get; set; }

        private int? _trimStartMs;
        private int? _trimEndMs;
        private double _durationMs;

        /// <summary>Début de la portion gardée (ms), null = depuis le début. Donnée serveur : le fichier
        /// stocké est le son COMPLET, la découpe n'est qu'un repère (voir TrimmedWaveStream).</summary>
        [JsonIgnore]
        public int? TrimStartMs
        {
            get => _trimStartMs;
            set { if (_trimStartMs != value) { _trimStartMs = value; OnTrimChanged(); } }
        }

        /// <summary>Fin de la portion gardée (ms), null = jusqu'à la fin.</summary>
        [JsonIgnore]
        public int? TrimEndMs
        {
            get => _trimEndMs;
            set { if (_trimEndMs != value) { _trimEndMs = value; OnTrimChanged(); } }
        }

        /// <summary>Durée du fichier complet (ms), connue une fois la mini-waveform calculée ; 0 sinon.</summary>
        [JsonIgnore]
        public double DurationMs
        {
            get => _durationMs;
            set { if (_durationMs != value) { _durationMs = value; OnTrimChanged(); } }
        }

        /// <summary>Vrai si ce son est découpé (une portion seulement est jouée).</summary>
        [JsonIgnore]
        public bool IsTrimmed => _trimStartMs is not null && _trimEndMs is not null;

        private void OnTrimChanged()
        {
            OnChanged(nameof(TrimStartMs));
            OnChanged(nameof(TrimEndMs));
            OnChanged(nameof(DurationMs));
            OnChanged(nameof(IsTrimmed));
            OnChanged(nameof(ButtonTooltip));
        }

        /// <summary>Infobulle du bouton : les gestes, et la portion jouée si le son est découpé.</summary>
        [JsonIgnore]
        public string ButtonTooltip
        {
            get
            {
                const string gestures = "Clic gauche : jouer dans le vocal • Clic droit : options • Glisser : déplacer/classer";
                if (!IsTrimmed) return gestures;

                static string Fmt(double ms) => $"{(int)(ms / 60000)}:{(int)(ms / 1000) % 60:D2}";
                var kept = Fmt(_trimEndMs!.Value - _trimStartMs!.Value);
                var range = $"{Fmt(_trimStartMs.Value)} → {Fmt(_trimEndMs.Value)}";
                var total = _durationMs > 0 ? $" sur {Fmt(_durationMs)}" : "";
                return gestures + $"\n✂ Découpé : {range}{total} (durée jouée {kept}) — clic droit › Redécouper";
            }
        }

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

        public SoundItem()
        {
            // Les propriétés dérivées ci-dessous se recalculent quand le sondage d'activité ajoute/retire un joueur.
            ActiveUsers.CollectionChanged += (_, _) =>
            {
                OnChanged(nameof(HasActivePlayers));
                OnChanged(nameof(HasExtraActivePlayers));
                OnChanged(nameof(PrimaryAvatarUrl));
                OnChanged(nameof(ExtraActiveUsersText));
                OnChanged(nameof(ActiveUsersTooltip));
            };
        }

        [JsonIgnore]
        public bool HasActivePlayers => ActiveUsers.Count > 0;

        [JsonIgnore]
        public bool HasExtraActivePlayers => ActiveUsers.Count > 1;

        /// <summary>Avatar affiché sur le bouton : celui de la première personne qui joue ce son (un seul, même s'ils
        /// sont plusieurs — les autres sont résumés par « +N »). Chaîne vide si personne ne joue.</summary>
        [JsonIgnore]
        public string PrimaryAvatarUrl => ActiveUsers.Count > 0 ? ActiveUsers[0].AvatarUrl : "";

        /// <summary>« +N » quand d'autres personnes jouent ce même son en même temps, sinon vide.</summary>
        [JsonIgnore]
        public string ExtraActiveUsersText => ActiveUsers.Count > 1 ? $"+{ActiveUsers.Count - 1}" : "";

        /// <summary>Noms de tous ceux qui jouent ce son, pour l'infobulle de l'avatar.</summary>
        [JsonIgnore]
        public string ActiveUsersTooltip => string.Join(", ", ActiveUsers.Select(u => u.Username));

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
