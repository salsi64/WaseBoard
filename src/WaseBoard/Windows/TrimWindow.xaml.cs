using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Shapes;
using NAudio.Wave;
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
    /// (volume, favori, catégories personnelles) et catégories partagées des serveurs.</summary>
    public sealed class EditExtras
    {
        public float Volume { get; init; } = 1f;
        public bool IsFavorite { get; init; }
        public IReadOnlyList<string> LocalCategories { get; init; } = Array.Empty<string>();
        public IReadOnlyCollection<string> SelectedLocalCategories { get; init; } = Array.Empty<string>();
        public IReadOnlyList<SharedCategoryChoice> SharedCategories { get; init; } = Array.Empty<SharedCategoryChoice>();
    }

    /// <summary>
    /// Fenêtre « Éditer le son » : nom, emoji, favori, catégories, volume et portion gardée, sur la waveform du son
    /// COMPLET. La découpe est non destructive : le fichier n'est jamais modifié (le serveur garde le son entier et ne
    /// mémorise que le début et la fin), donc on peut recouper plus tard en rouvrant cette fenêtre.
    /// </summary>
    public partial class TrimWindow : Window
    {
        private const double CanvasWidth = 496;
        private const double CanvasHeight = 110;
        private const double HandleWidth = 14;

        private readonly string _sourceFilePath;
        private readonly TrimWindowMode _mode;
        private readonly TimeSpan? _initialStart;
        private readonly TimeSpan? _initialEnd;
        private TimeSpan _duration = TimeSpan.FromSeconds(1);
        private TimeSpan _trimStart = TimeSpan.Zero;
        private TimeSpan _trimEnd = TimeSpan.FromSeconds(1);

        private WaveOutEvent? _previewOutput;
        private WaveStream? _previewStream;

        private readonly List<Rectangle> _bars = new();
        private readonly List<string> _localCategories = new();
        private readonly HashSet<string> _selectedLocal = new(StringComparer.Ordinal);
        private readonly List<SharedCategoryChoice> _sharedChoices = new();
        private bool _isFavorite;
        private DateTime _emojiPopupClosedAt = DateTime.MinValue;
        private DateTime _categoryPopupClosedAt = DateTime.MinValue;

        public string ResultName { get; private set; } = string.Empty;

        /// <summary>Emoji choisi (chaîne vide = aucun, possible seulement en mode Edit).</summary>
        public string ResultEmoji { get; private set; } = string.Empty;

        /// <summary>Portion gardée en millisecondes ; null/null = le son entier (aucune découpe).</summary>
        public int? ResultTrimStartMs { get; private set; }
        public int? ResultTrimEndMs { get; private set; }

        /// <summary>Volume individuel choisi (1.0 = 100 %). Mode Edit uniquement.</summary>
        public float ResultVolume { get; private set; } = 1f;
        public bool ResultFavorite { get; private set; }
        public IReadOnlyCollection<string> ResultLocalCategories => _selectedLocal;
        public IReadOnlyList<SharedCategoryChoice> ResultSharedCategories => _sharedChoices;

        public TrimWindow(string sourceFilePath, string suggestedName, TrimWindowMode mode = TrimWindowMode.Add,
            TimeSpan? initialStart = null, TimeSpan? initialEnd = null, string? initialEmoji = null,
            EditExtras? extras = null)
        {
            InitializeComponent();
            _sourceFilePath = sourceFilePath;
            _mode = mode;
            _initialStart = initialStart;
            _initialEnd = initialEnd;
            NameBox.Text = suggestedName;

            Palette.EmojiChosen += emoji => { EmojiBox.Text = emoji; EmojiPopup.IsOpen = false; };
            EmojiBox.Text = initialEmoji ?? "";

            if (mode == TrimWindowMode.Edit)
            {
                Title = "Éditer le son";
                ChromeTitleBar.TitleText = "Éditer le son";
                ConfirmButton.Content = "Enregistrer";
                HintText.Text = "Le son complet est conservé : déplacez les poignées pour changer la partie jouée, ou « Tout garder » pour retrouver le son entier.";

                if (extras is not null)
                {
                    EditOnlyPanel.Visibility = Visibility.Visible;
                    FavoriteButton.Visibility = Visibility.Visible;

                    _isFavorite = extras.IsFavorite;
                    UpdateFavoriteVisual();

                    VolumeSlider.Value = Math.Clamp(Math.Round(extras.Volume * 100), 0, 200);

                    _localCategories.AddRange(extras.LocalCategories);
                    foreach (var c in extras.SelectedLocalCategories) _selectedLocal.Add(c);
                    _sharedChoices.AddRange(extras.SharedCategories);
                    BuildCategoryList();
                }
            }
            ResultVolume = (float)(VolumeSlider.Value / 100.0);
            UpdateConfirmEnabled();

            Loaded += TrimWindow_Loaded;
        }

        private async void TrimWindow_Loaded(object sender, RoutedEventArgs e)
        {
            try
            {
                var data = await Task.Run(() => AudioTrimService.ComputeWaveform(_sourceFilePath));
                _duration = data.Duration.TotalMilliseconds > 0 ? data.Duration : TimeSpan.FromSeconds(1);

                // Mode Edit : on repart de la portion actuellement gardée (bornée à la durée du fichier).
                _trimStart = _initialStart is { } s ? Clamp(s, TimeSpan.Zero, _duration) : TimeSpan.Zero;
                _trimEnd = _initialEnd is { } en ? Clamp(en, TimeSpan.Zero, _duration) : _duration;
                if (_trimEnd <= _trimStart) { _trimStart = TimeSpan.Zero; _trimEnd = _duration; }

                DrawWaveform(data.Peaks);
                PositionHandles();
                UpdateMasksAndLabel();
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

        private void DrawWaveform(float[] peaks)
        {
            if (peaks.Length == 0) return;

            double barWidth = CanvasWidth / peaks.Length;
            double centerY = CanvasHeight / 2;
            var fill = (Brush)FindResource("AccentBrush");

            for (int i = 0; i < peaks.Length; i++)
            {
                double barHeight = Math.Max(2, peaks[i] * (CanvasHeight - 10));
                var bar = new Rectangle
                {
                    Width = Math.Max(1, barWidth - 1),
                    Height = barHeight,
                    Fill = fill,
                    Opacity = 0.85,
                    RadiusX = 1,
                    RadiusY = 1,
                    RenderTransformOrigin = new Point(0.5, 0.5)
                };
                Canvas.SetLeft(bar, i * barWidth);
                Canvas.SetTop(bar, centerY - barHeight / 2);

                // Inséré en dessous des masques/poignées (déjà présents dans le XAML) pour rester en arrière-plan.
                WaveformCanvas.Children.Insert(0, bar);
                _bars.Add(bar);
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
            UpdateConfirmEnabled();
        }

        /// <summary>À l'ajout, l'emoji est obligatoire (comportement historique : chaque son en a un) ;
        /// en modification on peut aussi le retirer.</summary>
        private void UpdateConfirmEnabled()
        {
            if (ConfirmButton is null || EmojiBox is null) return; // encore en cours d'InitializeComponent
            var needsEmoji = _mode == TrimWindowMode.Add;
            ConfirmButton.IsEnabled = !needsEmoji || !string.IsNullOrWhiteSpace(EmojiBox.Text);
            ConfirmButton.ToolTip = ConfirmButton.IsEnabled ? null : "Choisissez un emoji pour ce son";
        }

        // ---------- Favori ----------

        private void FavoriteButton_Click(object sender, RoutedEventArgs e)
        {
            _isFavorite = !_isFavorite;
            UpdateFavoriteVisual();
        }

        private void UpdateFavoriteVisual()
        {
            FavoriteGlyph.Text = _isFavorite ? "★" : "☆";
            FavoriteGlyph.Foreground = _isFavorite ? new SolidColorBrush(Color.FromRgb(0xFB, 0xBF, 0x24)) : (Brush)FindResource("TextBrush");
            FavoriteGlyph.Opacity = _isFavorite ? 1.0 : 0.7;
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
        }

        /// <summary>La waveform grandit/rétrécit avec le volume : 0 % → ×0,4 · 100 % → ×1 · 200 % → ×1,6 (rendu seulement).</summary>
        private void ApplyVolumeVisuals()
        {
            var factor = 0.4 + (VolumeSlider.Value / 200.0) * 1.2;
            foreach (var bar in _bars) bar.RenderTransform = new ScaleTransform(1, factor);
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
            UpdateMasksAndLabel();
        }

        private void EndHandle_DragDelta(object sender, DragDeltaEventArgs e)
        {
            double newX = Canvas.GetLeft(EndHandle) + HandleWidth / 2 + e.HorizontalChange;
            double minX = TimeToX(_trimStart) + 20;
            newX = Math.Clamp(newX, Math.Min(CanvasWidth, minX), CanvasWidth);

            _trimEnd = XToTime(newX);
            Canvas.SetLeft(EndHandle, newX - HandleWidth / 2);
            UpdateMasksAndLabel();
        }

        private void UpdateMasksAndLabel()
        {
            double startX = TimeToX(_trimStart);
            double endX = TimeToX(_trimEnd);

            LeftMaskRect.Width = Math.Max(0, startX);
            Canvas.SetLeft(LeftMaskRect, 0);

            RightMaskRect.Width = Math.Max(0, CanvasWidth - endX);
            Canvas.SetLeft(RightMaskRect, endX);

            var selected = _trimEnd - _trimStart;
            TimeLabel.Text = $"{Format(_trimStart)} → {Format(_trimEnd)}  ·  {Format(selected)} gardées sur {Format(_duration)}";
        }

        private static string Format(TimeSpan t) => $"{(int)t.TotalMinutes}:{t.Seconds:D2}";

        private void ResetButton_Click(object sender, RoutedEventArgs e)
        {
            _trimStart = TimeSpan.Zero;
            _trimEnd = _duration;
            PositionHandles();
            UpdateMasksAndLabel();
        }

        // ---------- Aperçu ----------

        private void PreviewButton_Click(object sender, RoutedEventArgs e)
        {
            StopPreview();

            try
            {
                // Même flux que la lecture locale réelle (TrimmedWaveStream) : l'aperçu correspond pile à ce
                // que fera le son une fois enregistré, sans fichier temporaire.
                var reader = AudioReaderFactory.OpenForPlayback(_sourceFilePath, out _);
                _previewStream = new TrimmedWaveStream(reader, _trimStart, _trimEnd);
                _previewOutput = new WaveOutEvent();
                _previewOutput.Init(_previewStream);
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

        // ---------- Validation ----------

        private void Confirm_Click(object sender, RoutedEventArgs e)
        {
            if ((_trimEnd - _trimStart).TotalMilliseconds < 100)
            {
                AlertDialog.Show(this, "La portion sélectionnée est trop courte.", "WaseBoard", AlertKind.Warning);
                return;
            }

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
