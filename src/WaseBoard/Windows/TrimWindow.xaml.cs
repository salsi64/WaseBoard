using System;
using System.IO;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Media;
using System.Windows.Shapes;
using System.Windows.Threading;
using NAudio.Wave;
using WaseBoard.Services;

namespace WaseBoard.Windows
{
    public partial class TrimWindow : Window
    {
        private const double CanvasWidth = 736;
        private const double CanvasHeight = 150;
        private const double HandleWidth = 10;

        private readonly string _sourceFilePath;
        private TimeSpan _duration = TimeSpan.FromSeconds(1);
        private TimeSpan _trimStart = TimeSpan.Zero;
        private TimeSpan _trimEnd = TimeSpan.FromSeconds(1);

        private WaveOutEvent? _previewOutput;
        private AudioFileReader? _previewReader;
        private string? _previewTempFile;

        /// <summary>Chemin du fichier à enregistrer (original si non découpé, ou WAV temporaire découpé). Null si annulé.</summary>
        public string? ResultFilePath { get; private set; }
        public string ResultName { get; private set; } = string.Empty;

        /// <summary>True si un fichier temporaire a été créé et doit être supprimé par l'appelant après usage.</summary>
        public bool ResultIsTemporaryFile { get; private set; }

        public TrimWindow(string sourceFilePath, string suggestedName)
        {
            InitializeComponent();
            _sourceFilePath = sourceFilePath;
            NameBox.Text = suggestedName;
            Loaded += TrimWindow_Loaded;
        }

        private async void TrimWindow_Loaded(object sender, RoutedEventArgs e)
        {
            try
            {
                var data = await Task.Run(() => AudioTrimService.ComputeWaveform(_sourceFilePath));
                _duration = data.Duration.TotalMilliseconds > 0 ? data.Duration : TimeSpan.FromSeconds(1);
                _trimStart = TimeSpan.Zero;
                _trimEnd = _duration;

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

        private void DrawWaveform(float[] peaks)
        {
            if (peaks.Length == 0) return;

            double barWidth = CanvasWidth / peaks.Length;
            double centerY = CanvasHeight / 2;

            for (int i = 0; i < peaks.Length; i++)
            {
                double barHeight = Math.Max(2, peaks[i] * (CanvasHeight - 10));
                var bar = new Rectangle
                {
                    Width = Math.Max(1, barWidth - 1),
                    Height = barHeight,
                    Fill = new SolidColorBrush(Color.FromRgb(0x9C, 0x8C, 0xFF))
                };
                Canvas.SetLeft(bar, i * barWidth);
                Canvas.SetTop(bar, centerY - barHeight / 2);

                // Inséré en dessous des masques/poignées (déjà présents dans le XAML) pour rester en arrière-plan.
                WaveformCanvas.Children.Insert(0, bar);
            }
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
            TimeLabel.Text = $"Début : {Format(_trimStart)} — Fin : {Format(_trimEnd)} — Durée sélectionnée : {Format(selected)}";
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
                // On exporte réellement la sélection avant de la jouer : c'est exactement le même
                // chemin de code que la validation finale (lecture séquentielle stricte, jamais de
                // seek), donc l'aperçu correspond toujours pile à ce que produira "Ajouter le son".
                _previewTempFile = AudioTrimService.ExportTrimmedWav(_sourceFilePath, _trimStart, _trimEnd);

                _previewReader = new AudioFileReader(_previewTempFile);
                _previewOutput = new WaveOutEvent();
                _previewOutput.Init(_previewReader);
                _previewOutput.PlaybackStopped += (_, _) => Dispatcher.BeginInvoke(CleanupPreviewTempFile);
                _previewOutput.Play();
            }
            catch (Exception ex)
            {
                AlertDialog.Show(this, "Impossible de lire l'aperçu :\n" + ex.Message,
                    "Erreur", AlertKind.Warning);
            }
        }

        private void StopPreview()
        {
            _previewOutput?.Stop();
            _previewOutput?.Dispose();
            _previewOutput = null;

            _previewReader?.Dispose();
            _previewReader = null;

            CleanupPreviewTempFile();
        }

        private void CleanupPreviewTempFile()
        {
            if (_previewTempFile is not null && File.Exists(_previewTempFile))
            {
                try { File.Delete(_previewTempFile); } catch { /* fichier temporaire, non bloquant */ }
            }
            _previewTempFile = null;
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

            if (isFullRange)
            {
                // Pas de découpe nécessaire : on garde le fichier original tel quel (pas de conversion).
                ResultFilePath = _sourceFilePath;
                ResultIsTemporaryFile = false;
            }
            else
            {
                ResultFilePath = AudioTrimService.ExportTrimmedWav(_sourceFilePath, _trimStart, _trimEnd);
                ResultIsTemporaryFile = true;
            }

            ResultName = string.IsNullOrWhiteSpace(NameBox.Text) ? "Son" : NameBox.Text.Trim();
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
