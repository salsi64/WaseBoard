using System;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Media;
using System.Windows.Shapes;
using NAudio.Wave;
using WaseBoard.Services;

namespace WaseBoard.Windows
{
    /// <summary>Ajout d'un nouveau son, ou modification d'un son existant (« Éditer »).</summary>
    public enum TrimWindowMode { Add, Edit }

    /// <summary>
    /// Fenêtre « Modifier le son » : nom, emoji et portion gardée, sur la waveform du son COMPLET. La découpe est
    /// non destructive : le fichier n'est jamais modifié (le serveur garde le son entier et ne mémorise que le
    /// début et la fin), donc on peut recouper plus tard en rouvrant cette fenêtre avec la sélection actuelle.
    /// </summary>
    public partial class TrimWindow : Window
    {
        private const double CanvasWidth = 736;
        private const double CanvasHeight = 150;
        private const double HandleWidth = 10;

        private readonly string _sourceFilePath;
        private readonly TrimWindowMode _mode;
        private readonly TimeSpan? _initialStart;
        private readonly TimeSpan? _initialEnd;
        private TimeSpan _duration = TimeSpan.FromSeconds(1);
        private TimeSpan _trimStart = TimeSpan.Zero;
        private TimeSpan _trimEnd = TimeSpan.FromSeconds(1);

        private WaveOutEvent? _previewOutput;
        private WaveStream? _previewStream;

        public string ResultName { get; private set; } = string.Empty;

        /// <summary>Emoji choisi (chaîne vide = aucun, possible seulement en mode Edit).</summary>
        public string ResultEmoji { get; private set; } = string.Empty;

        /// <summary>Portion gardée en millisecondes ; null/null = le son entier (aucune découpe).</summary>
        public int? ResultTrimStartMs { get; private set; }
        public int? ResultTrimEndMs { get; private set; }

        public TrimWindow(string sourceFilePath, string suggestedName, TrimWindowMode mode = TrimWindowMode.Add,
            TimeSpan? initialStart = null, TimeSpan? initialEnd = null, string? initialEmoji = null)
        {
            InitializeComponent();
            _sourceFilePath = sourceFilePath;
            _mode = mode;
            _initialStart = initialStart;
            _initialEnd = initialEnd;
            NameBox.Text = suggestedName;

            Palette.EmojiChosen += emoji => EmojiBox.Text = emoji;
            EmojiBox.Text = initialEmoji ?? "";

            if (mode == TrimWindowMode.Edit)
            {
                Title = "Éditer le son";
                ChromeTitleBar.TitleText = "Éditer le son";
                ConfirmButton.Content = "Enregistrer";
                HintText.Text = "Le son complet est conservé : déplacez les poignées pour changer la partie jouée, ou « Tout garder » pour retrouver le son entier.";
            }
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
                    Opacity = 0.85
                };
                Canvas.SetLeft(bar, i * barWidth);
                Canvas.SetTop(bar, centerY - barHeight / 2);

                // Inséré en dessous des masques/poignées (déjà présents dans le XAML) pour rester en arrière-plan.
                WaveformCanvas.Children.Insert(0, bar);
            }
        }

        // ---------- Emoji ----------

        private void EmojiBox_TextChanged(object sender, TextChangedEventArgs e)
        {
            var emoji = EmojiBox.Text.Trim();
            // Image plutôt que texte (voir EmojiImageResolver) ; vide = pas d'aperçu.
            EmojiPreview.Source = string.IsNullOrEmpty(emoji) ? null : EmojiImageResolver.Resolve(emoji);
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
            TimeLabel.Text = $"Début : {Format(_trimStart)} — Fin : {Format(_trimEnd)} — Durée sélectionnée : {Format(selected)} (son complet : {Format(_duration)})";
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
