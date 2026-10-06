using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Shapes;
using NAudio.Wave;
using NAudio.Wave.SampleProviders;
using WaseBoard.Services;

namespace WaseBoard.Windows
{
    /// <summary>Ajout d'un nouveau son, ou modification d'un son existant (« Éditer »).</summary>
    public enum TrimWindowMode { Add, Edit }

    /// <summary>Catégorie partagée d'un serveur proposée dans la fenêtre d'édition ; Locked = serveur d'origine du son
    /// (le son en fait toujours partie, on ne peut pas l'en retirer).</summary>
    public sealed class SharedCategoryChoice
    {
        public string GuildId { get; init; } = "";
        public string GuildName { get; init; } = "";
        public bool IsMember { get; set; }
        public bool Locked { get; init; }
    }

    /// <summary>Ce que la fenêtre d'édition sait d'un son existant en plus de son nom/emoji/découpe : réglages locaux
    /// (volume, couleur, favori, catégories personnelles), catégories partagées des serveurs et nom du fichier.</summary>
    public sealed class EditExtras
    {
        /// <summary>Faux pour qui n'est ni l'auteur du son ni admin de son serveur : la fenêtre ne propose alors que les
        /// réglages LOCAUX (favori, couleur, volume, catégories) ; nom, emoji, découpe, fichier et suppression sont partagés.</summary>
        public bool CanEditShared { get; init; } = true;
        public float Volume { get; init; } = 1f;
        public bool IsFavorite { get; init; }
        public string? ColorHex { get; init; }
        public string FileName { get; init; } = "";
        public IReadOnlyList<string> LocalCategories { get; init; } = Array.Empty<string>();
        public IReadOnlyCollection<string> SelectedLocalCategories { get; init; } = Array.Empty<string>();
        public IReadOnlyList<SharedCategoryChoice> SharedCategories { get; init; } = Array.Empty<SharedCategoryChoice>();
    }

    /// <summary>
    /// Fenêtre « Éditer le son » : nom, emoji, favori, couleur, catégories, volume et portion gardée, sur la waveform du
    /// son COMPLET. La découpe est non destructive : le fichier n'est jamais modifié (le serveur garde le son entier et
    /// ne mémorise que le début et la fin), donc on peut recouper plus tard en rouvrant cette fenêtre.
    /// </summary>
    public partial class TrimWindow : Window
    {
        private const double CanvasWidth = 374;
        private const double CanvasHeight = 58;
        private const double HandleWidth = 14;
        private const double BarWidth = 6;
        private const double BarGap = 2;

        private static readonly string[] SwatchColors =
        {
            "#F472B6", "#38BDF8", "#34D399", "#FBBF24", "#F87171", "#A78BFA", "#22D3EE", "#94A3B8"
        };

        private bool _canEditShared = true;
        private string _sourceFilePath;
        private readonly string _originalSourcePath;
        private WaveformData? _originalData;
        private string _originalFileName = "";
        private string? _replacementPath;
        private readonly TrimWindowMode _mode;
        private readonly TimeSpan? _initialStart;
        private readonly TimeSpan? _initialEnd;
        private TimeSpan _duration = TimeSpan.FromSeconds(1);
        private TimeSpan _trimStart = TimeSpan.Zero;
        private TimeSpan _trimEnd = TimeSpan.FromSeconds(1);
        private TimeSpan _loadedStart = TimeSpan.Zero;
        private TimeSpan _loadedEnd = TimeSpan.FromSeconds(1);

        private WaveOutEvent? _previewOutput;
        private WaveStream? _previewStream;
        private bool _previewMuted;

        private readonly List<(Rectangle Bar, double CenterX)> _bars = new();
        private Brush _barDimBrush = Brushes.Gray;
        private Brush _barSelectedBrush = Brushes.White;

        private readonly List<string> _localCategories = new();
        private readonly HashSet<string> _selectedLocal = new(StringComparer.Ordinal);
        private readonly List<SharedCategoryChoice> _sharedChoices = new();
        private bool _isFavorite;
        private string? _colorHex;
        private DateTime _emojiPopupClosedAt = DateTime.MinValue;
        private DateTime _categoryPopupClosedAt = DateTime.MinValue;

        // Valeurs de départ (mode Edit) : « Enregistrer » ne s'active que si quelque chose a changé.
        private bool _initialized;
        private string _initName = "";
        private string _initEmoji = "";
        private bool _initFavorite;
        private string? _initColor;
        private double _initVolume = 100;
        private HashSet<string> _initLocal = new();
        private Dictionary<string, bool> _initShared = new();

        public string ResultName { get; private set; } = string.Empty;

        /// <summary>Emoji choisi (chaîne vide = aucun, possible seulement en mode Edit).</summary>
        public string ResultEmoji { get; private set; } = string.Empty;

        /// <summary>Portion gardée en millisecondes ; null/null = le son entier (aucune découpe).</summary>
        public int? ResultTrimStartMs { get; private set; }
        public int? ResultTrimEndMs { get; private set; }

        /// <summary>Volume individuel choisi (1.0 = 100 %). Mode Edit uniquement.</summary>
        public float ResultVolume { get; private set; } = 1f;
        public bool ResultFavorite { get; private set; }

        /// <summary>Couleur choisie ("#RRGGBB"), null = automatique.</summary>
        public string? ResultColorHex { get; private set; }
        public IReadOnlyCollection<string> ResultLocalCategories => _selectedLocal;
        public IReadOnlyList<SharedCategoryChoice> ResultSharedCategories => _sharedChoices;

        /// <summary>L'utilisateur a demandé (et confirmé) la suppression du son : la fenêtre se ferme en OK sans rien d'autre à appliquer.</summary>
        public bool ResultDeleteRequested { get; private set; }

        /// <summary>Chemin du nouveau fichier audio choisi pour remplacer celui du son (null = on garde le fichier actuel).
        /// Quand il est renseigné, la découpe renvoyée vise ce NOUVEAU fichier.</summary>
        public string? ResultReplacementFile => _replacementPath;

        public TrimWindow(string sourceFilePath, string suggestedName, TrimWindowMode mode = TrimWindowMode.Add,
            TimeSpan? initialStart = null, TimeSpan? initialEnd = null, string? initialEmoji = null,
            EditExtras? extras = null)
        {
            InitializeComponent();
            _sourceFilePath = sourceFilePath;
            _originalSourcePath = sourceFilePath;
            _mode = mode;
            _initialStart = initialStart;
            _initialEnd = initialEnd;
            NameBox.Text = suggestedName;

            Palette.EmojiChosen += emoji => { EmojiBox.Text = emoji; EmojiPopup.IsOpen = false; };
            EmojiBox.Text = initialEmoji ?? "";

            if (mode == TrimWindowMode.Add)
            {
                Title = "Ajouter un son";
                HeadingText.Text = "Ajouter un son";
            }
            else
            {
                ConfirmButton.Content = "Enregistrer";
                HintText.Visibility = Visibility.Collapsed;

                if (extras is not null)
                {
                    _canEditShared = extras.CanEditShared;
                    EditOnlyPanel.Visibility = Visibility.Visible;
                    FilePanel.Visibility = extras.CanEditShared ? Visibility.Visible : Visibility.Collapsed;
                    FavoriteButton.Visibility = Visibility.Visible;

                    if (!extras.CanEditShared)
                    {
                        // Son d'un autre : seuls les réglages locaux sont modifiables, le reste est partagé par tout le serveur.
                        Title = "Personnaliser le son";
                        HeadingText.Text = "Personnaliser le son";
                        NameBox.IsReadOnly = true;
                        NameBox.ToolTip = "Seuls l'auteur du son et les admins du serveur peuvent le renommer";
                        ThumbButton.IsEnabled = false;
                        ThumbButton.ToolTip = null;
                        EditBadge.Visibility = Visibility.Collapsed;
                        TrimPanel.Visibility = Visibility.Collapsed;
                    }

                    _isFavorite = extras.IsFavorite;
                    UpdateFavoriteVisual();

                    _colorHex = extras.ColorHex;
                    BuildSwatches();
                    ApplyThumbColor();

                    _originalFileName = extras.FileName;
                    FileNameText.Text = extras.FileName;

                    VolumeSlider.Value = Math.Clamp(Math.Round(extras.Volume * 100), 0, 200);

                    _localCategories.AddRange(extras.LocalCategories);
                    foreach (var c in extras.SelectedLocalCategories) _selectedLocal.Add(c);
                    _sharedChoices.AddRange(extras.SharedCategories);
                    BuildCategoryList();
                }
            }

            _initName = NameBox.Text;
            _initEmoji = EmojiBox.Text.Trim();
            _initFavorite = _isFavorite;
            _initColor = _colorHex;
            _initVolume = VolumeSlider.Value;
            _initLocal = new HashSet<string>(_selectedLocal);
            _initShared = _sharedChoices.ToDictionary(s => s.GuildId, s => s.IsMember);
            _initialized = true;

            ResultVolume = (float)(VolumeSlider.Value / 100.0);
            RefreshDirty();

            Loaded += TrimWindow_Loaded;
        }

        private async void TrimWindow_Loaded(object sender, RoutedEventArgs e)
        {
            if (!_canEditShared)
            {
                // Pas de découpe à proposer : inutile d'analyser le fichier (et de le télécharger).
                LoadingText.Visibility = Visibility.Collapsed;
                return;
            }
            try
            {
                var data = await Task.Run(() => AudioTrimService.ComputeWaveform(_sourceFilePath));
                _originalData = data;
                // Mode Edit : on repart de la portion actuellement gardée (bornée à la durée du fichier).
                ShowWaveform(data, _initialStart, _initialEnd);
                RefreshDirty();
            }
            catch (Exception ex)
            {
                AlertDialog.Show(this, "Impossible d'analyser ce fichier audio :\n" + ex.Message,
                    "Erreur", AlertKind.Error);
                DialogResult = false;
                Close();
                return;
            }
            finally
            {
                LoadingText.Visibility = Visibility.Collapsed;
            }
        }

        private static TimeSpan Clamp(TimeSpan value, TimeSpan min, TimeSpan max) =>
            value < min ? min : value > max ? max : value;

        /// <summary>Affiche la waveform d'un fichier avec la portion gardée donnée (null = le fichier entier).</summary>
        private void ShowWaveform(WaveformData data, TimeSpan? start, TimeSpan? end)
        {
            _duration = data.Duration.TotalMilliseconds > 0 ? data.Duration : TimeSpan.FromSeconds(1);
            _trimStart = start is { } s ? Clamp(s, TimeSpan.Zero, _duration) : TimeSpan.Zero;
            _trimEnd = end is { } en ? Clamp(en, TimeSpan.Zero, _duration) : _duration;
            if (_trimEnd <= _trimStart) { _trimStart = TimeSpan.Zero; _trimEnd = _duration; }
            _loadedStart = _trimStart;
            _loadedEnd = _trimEnd;

            foreach (var (bar, _) in _bars) WaveformCanvas.Children.Remove(bar);
            _bars.Clear();
            DrawWaveform(data.Peaks);
            PositionHandles();
            UpdateSelection();
            UpdateFileInfoText();
        }

        private void UpdateFileInfoText() =>
            FileInfoText.Text = _replacementPath is null
                ? $"Durée totale : {Seconds(_duration)}"
                : $"Nouveau fichier · {Seconds(_duration)}";

        // ---------- Remplacement du fichier audio ----------

        private async void ReplaceFileButton_Click(object sender, RoutedEventArgs e)
        {
            var patterns = string.Join(";", SoundLibraryService.SupportedExtensions.Select(x => "*" + x));
            var dialog = new Microsoft.Win32.OpenFileDialog
            {
                Title = "Choisir le nouveau fichier audio",
                Filter = $"Fichiers audio ({patterns})|{patterns}|Tous les fichiers|*.*"
            };
            if (dialog.ShowDialog(this) != true) return;

            await ApplyReplacementFileAsync(dialog.FileName);
        }

        /// <summary>Prend ce fichier comme nouveau fichier audio du son (appliqué à l'enregistrement). Faux si refusé
        /// (format inconnu ou fichier illisible) : la fenêtre reste alors exactement comme elle était.</summary>
        private async Task<bool> ApplyReplacementFileAsync(string path)
        {
            var extension = System.IO.Path.GetExtension(path).ToLowerInvariant();
            if (!SoundLibraryService.SupportedExtensions.Contains(extension))
            {
                AlertDialog.Show(this, $"Ce format n'est pas pris en charge ({extension}). Formats acceptés : {string.Join(", ", SoundLibraryService.SupportedExtensions)}.",
                    "WaseBoard", AlertKind.Warning);
                return false;
            }

            // On analyse AVANT de bouger quoi que ce soit : un fichier illisible laisse la fenêtre telle quelle.
            WaveformData data;
            try
            {
                ReplaceFileButton.IsEnabled = false;
                data = await Task.Run(() => AudioTrimService.ComputeWaveform(path));
            }
            catch (Exception ex)
            {
                AlertDialog.Show(this, "Impossible d'analyser ce fichier audio :\n" + ex.Message, "Erreur", AlertKind.Error);
                return false;
            }
            finally
            {
                ReplaceFileButton.IsEnabled = true;
            }

            StopPreview();
            _sourceFilePath = path;
            _replacementPath = path;
            FileNameText.Text = System.IO.Path.GetFileName(path);
            RevertFileButton.Visibility = Visibility.Visible;
            ShowWaveform(data, null, null); // l'ancienne découpe visait l'ancien fichier
            RefreshDirty();
            return true;
        }

        private void RevertFileButton_Click(object sender, RoutedEventArgs e)
        {
            if (_replacementPath is null || _originalData is null) return;

            StopPreview();
            _sourceFilePath = _originalSourcePath;
            _replacementPath = null;
            FileNameText.Text = _originalFileName;
            RevertFileButton.Visibility = Visibility.Collapsed;
            ShowWaveform(_originalData, _initialStart, _initialEnd);
            RefreshDirty();
        }

        /// <summary>Waveform en grosses barres arrondies (une cinquantaine, comme la maquette) : chaque barre prend le
        /// pic le plus fort de sa tranche. Les barres dans la zone gardée sont en dégradé d'accent, les autres en sourdine.</summary>
        private void DrawWaveform(float[] peaks)
        {
            if (peaks.Length == 0) return;

            var accent = ((SolidColorBrush)FindResource("AccentBrush")).Color;
            var accent2 = ((SolidColorBrush)FindResource("Accent2Brush")).Color;
            _barSelectedBrush = new LinearGradientBrush(accent, accent2, 90);
            _barDimBrush = (Brush)FindResource("TrackBrush");

            int count = Math.Max(2, (int)Math.Floor((CanvasWidth + BarGap) / (BarWidth + BarGap)));
            double stride = (CanvasWidth - BarWidth) / (count - 1);
            double centerY = CanvasHeight / 2;
            float overallMax = Math.Max(0.01f, peaks.Max());

            for (int i = 0; i < count; i++)
            {
                int from = i * peaks.Length / count;
                int to = Math.Max(from + 1, (i + 1) * peaks.Length / count);
                float peak = 0;
                for (int p = from; p < Math.Min(to, peaks.Length); p++) peak = Math.Max(peak, peaks[p]);

                double barHeight = Math.Max(5, peak / overallMax * (CanvasHeight - 14));
                var bar = new Rectangle
                {
                    Width = BarWidth,
                    Height = barHeight,
                    RadiusX = 2,
                    RadiusY = 2,
                    Fill = _barDimBrush,
                    RenderTransformOrigin = new Point(0.5, 0.5)
                };
                double left = i * stride;
                Canvas.SetLeft(bar, left);
                Canvas.SetTop(bar, centerY - barHeight / 2);

                // Inséré en dessous de la sélection et des poignées (déjà présentes dans le XAML).
                WaveformCanvas.Children.Insert(0, bar);
                _bars.Add((bar, left + BarWidth / 2));
            }
            ApplyVolumeVisuals();
        }

        // ---------- Emoji ----------

        private void ThumbButton_Click(object sender, RoutedEventArgs e) => TogglePopup(EmojiPopup, _emojiPopupClosedAt);

        private void EmojiPopup_Closed(object? sender, EventArgs e) => _emojiPopupClosedAt = DateTime.UtcNow;

        /// <summary>Ouvre/ferme un popup « StaysOpen=False » depuis son bouton : sans ce garde-fou, le clic qui le ferme
        /// (clic extérieur) le rouvre aussitôt via le Click du même bouton.</summary>
        private static void TogglePopup(Popup popup, DateTime closedAt)
        {
            if (popup.IsOpen) { popup.IsOpen = false; return; }
            if ((DateTime.UtcNow - closedAt).TotalMilliseconds < 200) return;
            popup.IsOpen = true;
        }

        private void EmojiBox_TextChanged(object sender, TextChangedEventArgs e)
        {
            if (EmojiPreview is null || EmojiPlaceholder is null) return; // encore en cours d'InitializeComponent
            var emoji = EmojiBox.Text.Trim();
            // Image plutôt que texte (voir EmojiImageResolver) ; vide = pas d'aperçu.
            EmojiPreview.Source = string.IsNullOrEmpty(emoji) ? null : EmojiImageResolver.Resolve(emoji);
            EmojiPlaceholder.Visibility = EmojiPreview.Source is null ? Visibility.Visible : Visibility.Collapsed;
            RefreshDirty();
        }

        private void NameBox_TextChanged(object sender, TextChangedEventArgs e) => RefreshDirty();

        /// <summary>Ajout : l'emoji est obligatoire (comportement historique : chaque son en a un). Modification :
        /// « Enregistrer » ne s'active que si quelque chose a changé par rapport à l'ouverture de la fenêtre.</summary>
        private void RefreshDirty()
        {
            if (!_initialized || ConfirmButton is null) return; // encore en cours d'initialisation

            if (_mode == TrimWindowMode.Add)
            {
                var hasEmoji = !string.IsNullOrWhiteSpace(EmojiBox.Text);
                ConfirmButton.IsEnabled = hasEmoji;
                ConfirmButton.ToolTip = hasEmoji ? null : "Choisissez un emoji pour ce son";
                return;
            }

            var trimChanged = Math.Abs((_trimStart - _loadedStart).TotalMilliseconds) > 10 ||
                              Math.Abs((_trimEnd - _loadedEnd).TotalMilliseconds) > 10;
            var dirty = NameBox.Text != _initName
                || EmojiBox.Text.Trim() != _initEmoji
                || _isFavorite != _initFavorite
                || _colorHex != _initColor
                || Math.Abs(VolumeSlider.Value - _initVolume) > 0.01
                || _replacementPath is not null
                || !_selectedLocal.SetEquals(_initLocal)
                || _sharedChoices.Any(s => _initShared.TryGetValue(s.GuildId, out var was) && was != s.IsMember)
                || trimChanged;
            ConfirmButton.IsEnabled = dirty;
            ConfirmButton.ToolTip = dirty ? null : "Aucune modification";
        }

        // ---------- Favori ----------

        private void FavoriteButton_Click(object sender, RoutedEventArgs e)
        {
            _isFavorite = !_isFavorite;
            UpdateFavoriteVisual();
            RefreshDirty();
        }

        private void UpdateFavoriteVisual()
        {
            FavoriteGlyph.Text = _isFavorite ? "★" : "☆";
            FavoriteGlyph.Foreground = _isFavorite ? new SolidColorBrush(Color.FromRgb(0xFB, 0xBF, 0x24)) : (Brush)FindResource("TextBrush");
            FavoriteGlyph.Opacity = _isFavorite ? 1.0 : 0.7;
        }

        // ---------- Couleur ----------

        private void BuildSwatches()
        {
            SwatchPanel.Children.Clear();
            foreach (var hex in SwatchColors)
            {
                var color = (Color)ColorConverter.ConvertFromString(hex);
                var selected = string.Equals(_colorHex, hex, StringComparison.OrdinalIgnoreCase);
                var swatch = new Button
                {
                    Style = (Style)FindResource("SwatchButton"),
                    Background = new SolidColorBrush(color),
                    BorderBrush = selected ? (Brush)FindResource("TextBrush") : Brushes.Transparent,
                    Content = selected ? new TextBlock { Text = "✓", FontWeight = FontWeights.ExtraBold, FontSize = 13, Foreground = new SolidColorBrush(Color.FromRgb(0x0B, 0x12, 0x20)) } : null,
                    ToolTip = selected ? "Cliquer pour revenir à la couleur automatique" : "Couleur du son"
                };
                swatch.Click += (_, _) =>
                {
                    // Recliquer sur la couleur choisie la désélectionne : retour à la couleur automatique.
                    _colorHex = selected ? null : hex;
                    BuildSwatches();
                    ApplyThumbColor();
                    RefreshDirty();
                };
                SwatchPanel.Children.Add(swatch);
            }
        }

        /// <summary>La vignette prend la couleur choisie (en dégradé vers l'accent voisin), sinon le dégradé d'accent de la palette.</summary>
        private void ApplyThumbColor()
        {
            if (_colorHex is null)
            {
                ThumbButton.SetResourceReference(BackgroundProperty, "AccentGradientBrush");
                return;
            }
            var accent2 = ((SolidColorBrush)FindResource("Accent2Brush")).Color;
            ThumbButton.Background = new LinearGradientBrush((Color)ColorConverter.ConvertFromString(_colorHex), accent2, new Point(0, 0), new Point(1, 1));
        }

        // ---------- Catégories ----------

        private void CategoryTrigger_Click(object sender, RoutedEventArgs e) => TogglePopup(CategoryPopup, _categoryPopupClosedAt);

        private void CategoryPopup_Closed(object? sender, EventArgs e)
        {
            _categoryPopupClosedAt = DateTime.UtcNow;
            NewCategoryRow.Visibility = Visibility.Collapsed;
            NewCategoryButton.Visibility = Visibility.Visible;
        }

        /// <summary>Reconstruit la liste : d'abord les serveurs (catégories partagées), puis les catégories personnelles.</summary>
        private void BuildCategoryList()
        {
            CategoryList.Children.Clear();

            if (_sharedChoices.Count > 0)
            {
                CategoryList.Children.Add(CreateGroupHeader("Serveurs"));
                foreach (var shared in _sharedChoices)
                {
                    var s = shared;
                    CategoryList.Children.Add(CreateCategoryRow("🌐 " + s.GuildName, s.IsMember, s.Locked,
                        s.Locked ? "Serveur d'origine du son : il en fait toujours partie" : null,
                        () => { s.IsMember = !s.IsMember; BuildCategoryList(); }));
                }
            }

            CategoryList.Children.Add(CreateGroupHeader("Mes catégories"));
            if (_localCategories.Count == 0)
            {
                CategoryList.Children.Add(new TextBlock
                {
                    Text = "Aucune catégorie pour l'instant.", FontSize = 12, Opacity = 0.55, Margin = new Thickness(10, 4, 10, 6),
                    Foreground = (Brush)FindResource("TextBrush")
                });
            }
            foreach (var name in _localCategories)
            {
                var n = name;
                CategoryList.Children.Add(CreateCategoryRow(n, _selectedLocal.Contains(n), false, null, () =>
                {
                    if (!_selectedLocal.Remove(n)) _selectedLocal.Add(n);
                    BuildCategoryList();
                }));
            }

            UpdateCategoryTriggerText();
            RefreshDirty();
        }

        private TextBlock CreateGroupHeader(string text) => new()
        {
            Text = text.ToUpperInvariant(), FontSize = 10.5, FontWeight = FontWeights.Bold, Opacity = 0.55,
            Margin = new Thickness(10, 6, 10, 4), Foreground = (Brush)FindResource("TextBrush")
        };

        private Button CreateCategoryRow(string label, bool selected, bool locked, string? tooltip, Action onToggle)
        {
            var grid = new Grid();
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            grid.Children.Add(new TextBlock { Text = label, VerticalAlignment = VerticalAlignment.Center, TextTrimming = TextTrimming.CharacterEllipsis });
            var check = new TextBlock
            {
                Text = "✓", FontWeight = FontWeights.ExtraBold, VerticalAlignment = VerticalAlignment.Center,
                Foreground = (Brush)FindResource("AccentBrush"), Visibility = selected ? Visibility.Visible : Visibility.Hidden
            };
            Grid.SetColumn(check, 1);
            grid.Children.Add(check);

            var button = new Button
            {
                Content = grid, HorizontalContentAlignment = HorizontalAlignment.Stretch, Padding = new Thickness(10, 7, 10, 7),
                Background = Brushes.Transparent, BorderThickness = new Thickness(0),
                Foreground = (Brush)FindResource("TextBrush"), IsEnabled = !locked, Opacity = locked ? 0.7 : 1.0, ToolTip = tooltip
            };
            button.Click += (_, _) => onToggle();
            return button;
        }

        private void UpdateCategoryTriggerText()
        {
            var names = _sharedChoices.Where(s => s.IsMember).Select(s => "🌐 " + s.GuildName)
                .Concat(_localCategories.Where(_selectedLocal.Contains)).ToList();
            CategoryTriggerText.Text = names.Count == 0 ? "Aucune" : names.Count == 1 ? names[0] : $"{names[0]} +{names.Count - 1}";
        }

        private void NewCategoryButton_Click(object sender, RoutedEventArgs e)
        {
            NewCategoryButton.Visibility = Visibility.Collapsed;
            NewCategoryRow.Visibility = Visibility.Visible;
            NewCategoryBox.Text = "";
            NewCategoryBox.Focus();
        }

        private void NewCategoryCancel_Click(object sender, RoutedEventArgs e)
        {
            NewCategoryRow.Visibility = Visibility.Collapsed;
            NewCategoryButton.Visibility = Visibility.Visible;
        }

        private void NewCategoryConfirm_Click(object sender, RoutedEventArgs e) => ConfirmNewCategory();

        private void NewCategoryBox_KeyDown(object sender, KeyEventArgs e)
        {
            if (e.Key == Key.Enter) { ConfirmNewCategory(); e.Handled = true; }
            else if (e.Key == Key.Escape) { NewCategoryCancel_Click(sender, e); e.Handled = true; }
        }

        private void ConfirmNewCategory()
        {
            var name = NewCategoryBox.Text.Trim();
            if (name.Length == 0) return;

            var existing = _localCategories.FirstOrDefault(c => string.Equals(c, name, StringComparison.OrdinalIgnoreCase));
            if (existing is null) _localCategories.Add(name);
            _selectedLocal.Add(existing ?? name);

            NewCategoryRow.Visibility = Visibility.Collapsed;
            NewCategoryButton.Visibility = Visibility.Visible;
            BuildCategoryList();
        }

        // ---------- Volume ----------

        private void VolumeSlider_ValueChanged(object sender, RoutedPropertyChangedEventArgs<double> e)
        {
            if (VolumeChip is null || ResetVolumeButton is null) return; // encore en cours d'InitializeComponent
            var percent = (int)Math.Round(VolumeSlider.Value);
            VolumeChip.Text = $"{percent}%";
            ResetVolumeButton.Visibility = percent == 100 ? Visibility.Hidden : Visibility.Visible;
            ApplyVolumeVisuals();
            RefreshDirty();
        }

        /// <summary>La waveform grandit/rétrécit avec le volume : 0 % → ×0,4 · 100 % → ×1 · 200 % → ×1,6 (rendu seulement).</summary>
        private void ApplyVolumeVisuals()
        {
            var factor = 0.4 + (VolumeSlider.Value / 200.0) * 1.2;
            foreach (var (bar, _) in _bars) bar.RenderTransform = new ScaleTransform(1, factor);
        }

        private void VolumeMinus_Click(object sender, RoutedEventArgs e) => VolumeSlider.Value = Math.Max(0, VolumeSlider.Value - 5);
        private void VolumePlus_Click(object sender, RoutedEventArgs e) => VolumeSlider.Value = Math.Min(200, VolumeSlider.Value + 5);
        private void ResetVolume_Click(object sender, RoutedEventArgs e) => VolumeSlider.Value = 100;

        // ---------- Poignées de découpe ----------

        private void PositionHandles()
        {
            Canvas.SetLeft(StartHandle, TimeToX(_trimStart) - HandleWidth / 2);
            Canvas.SetLeft(EndHandle, TimeToX(_trimEnd) - HandleWidth / 2);
        }

        private double TimeToX(TimeSpan time) =>
            Math.Clamp(time.TotalSeconds / _duration.TotalSeconds * CanvasWidth, 0, CanvasWidth);

        private TimeSpan XToTime(double x) =>
            TimeSpan.FromSeconds(Math.Clamp(x / CanvasWidth, 0, 1) * _duration.TotalSeconds);

        private void StartHandle_DragDelta(object sender, DragDeltaEventArgs e)
        {
            double newX = Canvas.GetLeft(StartHandle) + HandleWidth / 2 + e.HorizontalChange;
            double maxX = TimeToX(_trimEnd) - 20; // garde un écart minimal avec la poignée de fin
            newX = Math.Clamp(newX, 0, Math.Max(0, maxX));

            _trimStart = XToTime(newX);
            Canvas.SetLeft(StartHandle, newX - HandleWidth / 2);
            UpdateSelection();
        }

        private void EndHandle_DragDelta(object sender, DragDeltaEventArgs e)
        {
            double newX = Canvas.GetLeft(EndHandle) + HandleWidth / 2 + e.HorizontalChange;
            double minX = TimeToX(_trimStart) + 20;
            newX = Math.Clamp(newX, Math.Min(CanvasWidth, minX), CanvasWidth);

            _trimEnd = XToTime(newX);
            Canvas.SetLeft(EndHandle, newX - HandleWidth / 2);
            UpdateSelection();
        }

        private void Handle_DragCompleted(object sender, DragCompletedEventArgs e) => RefreshDirty();

        /// <summary>Repositionne la zone gardée (teinte + liserés) et recolore les barres selon qu'elles sont dedans ou dehors.</summary>
        private void UpdateSelection()
        {
            double startX = TimeToX(_trimStart);
            double endX = TimeToX(_trimEnd);

            Canvas.SetLeft(SelectionRect, startX);
            SelectionRect.Width = Math.Max(0, endX - startX);
            Canvas.SetLeft(SelectionStartLine, Math.Max(0, startX - 1));
            Canvas.SetLeft(SelectionEndLine, Math.Min(CanvasWidth - 2, endX - 1));

            foreach (var (bar, centerX) in _bars)
                bar.Fill = centerX >= startX && centerX <= endX ? _barSelectedBrush : _barDimBrush;

            TimeLabel.Text = $"{Seconds(_trimStart)} – {Seconds(_trimEnd)}";
        }

        private static string Seconds(TimeSpan t) => t.TotalSeconds.ToString("0.0", CultureInfo.InvariantCulture) + "s";

        private static string Format(TimeSpan t) => $"{(int)t.TotalMinutes}:{t.Seconds:D2}";

        private void ResetButton_Click(object sender, RoutedEventArgs e)
        {
            _trimStart = TimeSpan.Zero;
            _trimEnd = _duration;
            PositionHandles();
            UpdateSelection();
            RefreshDirty();
        }

        // ---------- Aperçu ----------

        private void MuteButton_Click(object sender, RoutedEventArgs e)
        {
            _previewMuted = !_previewMuted;
            MuteButton.Content = _previewMuted ? "🔇" : "🔊";
            MuteButton.ToolTip = _previewMuted ? "Réactiver le son de l'aperçu" : "Couper le son de l'aperçu";
        }

        private void PreviewButton_Click(object sender, RoutedEventArgs e)
        {
            StopPreview();

            try
            {
                // Même flux que la lecture locale réelle (TrimmedWaveStream) : l'aperçu correspond pile à ce que fera
                // le son une fois enregistré, volume choisi compris (le curseur passe jusqu'à 200 %).
                var reader = AudioReaderFactory.OpenForPlayback(_sourceFilePath, out _);
                _previewStream = new TrimmedWaveStream(reader, _trimStart, _trimEnd);
                var volume = new VolumeSampleProvider(_previewStream.ToSampleProvider())
                {
                    Volume = _previewMuted ? 0f : (float)(VolumeSlider.Value / 100.0)
                };
                _previewOutput = new WaveOutEvent();
                _previewOutput.Init(new SampleToWaveProvider16(volume));
                _previewOutput.PlaybackStopped += (_, _) => Dispatcher.BeginInvoke(ReleasePreview);
                _previewOutput.Play();
            }
            catch (Exception ex)
            {
                ReleasePreview();
                AlertDialog.Show(this, "Impossible de lire l'aperçu :\n" + ex.Message,
                    "Erreur", AlertKind.Warning);
            }
        }

        private void StopPreview()
        {
            try { _previewOutput?.Stop(); } catch { /* déjà arrêté */ }
            ReleasePreview();
        }

        /// <summary>Libère la sortie et le flux de l'aperçu (idempotent : appelé à la fois à l'arrêt et à la fin naturelle).</summary>
        private void ReleasePreview()
        {
            _previewOutput?.Dispose();
            _previewOutput = null;
            _previewStream?.Dispose();
            _previewStream = null;
        }

        // ---------- Suppression ----------

        private void DeleteButton_Click(object sender, RoutedEventArgs e)
        {
            var confirmed = ConfirmDialog.Show(this,
                $"Supprimer « {NameBox.Text.Trim()} » du catalogue partagé ?\nUn administrateur du serveur pourra le restaurer depuis la corbeille.");
            if (!confirmed) return;

            StopPreview();
            ResultDeleteRequested = true;
            DialogResult = true;
            Close();
        }

        // ---------- Validation ----------

        private void Confirm_Click(object sender, RoutedEventArgs e)
        {
            if ((_trimEnd - _trimStart).TotalMilliseconds < 100)
            {
                AlertDialog.Show(this, "La portion sélectionnée est trop courte.", "WaseBoard", AlertKind.Warning);
                return;
            }

            // Le remplacement est le seul changement qu'on ne peut pas défaire : confirmation ici, pour pouvoir rester dans la fenêtre.
            if (_replacementPath is not null && !ConfirmDialog.Show(this,
                    $"Remplacer le fichier audio de « {NameBox.Text.Trim()} » ?\nLe nouveau fichier sera utilisé par tous les membres du serveur ; l'ancien n'est pas conservé."))
                return;

            StopPreview();

            bool isFullRange = _trimStart <= TimeSpan.FromMilliseconds(20) &&
                               _trimEnd >= _duration - TimeSpan.FromMilliseconds(20);

            // Son entier : aucune découpe à enregistrer (le serveur garde le fichier complet de toute façon).
            ResultTrimStartMs = isFullRange ? null : (int)Math.Round(_trimStart.TotalMilliseconds);
            ResultTrimEndMs = isFullRange ? null : (int)Math.Round(_trimEnd.TotalMilliseconds);

            ResultName = string.IsNullOrWhiteSpace(NameBox.Text) ? "Son" : NameBox.Text.Trim();
            ResultEmoji = EmojiBox.Text.Trim();
            ResultVolume = (float)(VolumeSlider.Value / 100.0);
            ResultFavorite = _isFavorite;
            ResultColorHex = _colorHex;
            DialogResult = true;
            Close();
        }

        private void Cancel_Click(object sender, RoutedEventArgs e)
        {
            StopPreview();
            DialogResult = false;
            Close();
        }

        private void Window_Closing(object sender, System.ComponentModel.CancelEventArgs e) => StopPreview();
    }
}
