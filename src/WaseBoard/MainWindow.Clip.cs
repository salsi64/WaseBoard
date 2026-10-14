using System;
using System.IO;
using System.Threading.Tasks;
using System.Windows;
using WaseBoard.Services;
using WaseBoard.Services.Localization;

namespace WaseBoard
{
    // Clip « les dernières secondes » : le PC garde en mémoire ce qu'il joue, un raccourci (ou le bouton de la barre d'outils)
    // en fait un fichier temporaire, puis la fenêtre d'ajout de son s'ouvre dessus pour découper, nommer et envoyer.
    public partial class MainWindow
    {
        /// <summary>En dessous de ce niveau crête (≈ −54 dBFS), le clip est considéré comme du silence : pas de fenêtre vide.</summary>
        private const double ClipSilentPeak = 0.002;

        private LoopbackClipService? _clip;
        private bool _clipBusy;
        private string? _clipHotkeyWarnedFor;

        private static string ClipsFolder => Path.Combine(Path.GetTempPath(), AppIdentity.DataFolderName + "-clips");

        private int ClipSeconds => LoopbackClipService.NormalizeSeconds(_library.Settings.ClipSeconds);

        /// <summary>Démarre, arrête ou redémarre la capture selon les réglages, et met le bouton à jour. Appelé au démarrage
        /// et après chaque « Enregistrer » des Paramètres.</summary>
        private void ApplyClipSettings()
        {
            if (_library.Settings.ClipEnabled)
            {
                if (_clip is null)
                {
                    _clip = new LoopbackClipService();
                    _clip.StateChanged += () => Dispatcher.BeginInvoke(() => UpdateClipButton());
                }
                _clip.Apply(true, ClipSeconds);
            }
            else
            {
                _clip?.Stop();
            }
            UpdateClipButton();
        }

        /// <summary>Le bouton n'existe que si la fonction est activée ; son point rouge est le voyant « la capture tourne »
        /// (la personne doit toujours pouvoir voir que le son du PC est écouté), et l'infobulle dit pourquoi si elle ne tourne pas.</summary>
        private void UpdateClipButton()
        {
            var settings = _library.Settings;
            var enabled = settings.ClipEnabled;
            var visibilityChanged = (ClipButton.Visibility == Visibility.Visible) != enabled;

            ClipButton.Visibility = enabled ? Visibility.Visible : Visibility.Collapsed;
            var capturing = enabled && _clip?.IsCapturing == true;
            ClipRecDot.Visibility = capturing ? Visibility.Visible : Visibility.Collapsed;

            var tip = capturing ? Loc.T("clip.tip_ready", ClipSeconds) : Loc.T("clip.tip_error", _clip?.LastError ?? "…");
            if (capturing && !string.IsNullOrEmpty(settings.ClipHotkey)) tip += Loc.T("clip.tip_hotkey", settings.ClipHotkey);
            ClipButton.ToolTip = tip;

            if (visibilityChanged) ApplyToolbarDensity();
        }

        /// <summary>Enregistre le raccourci du clip (réglage ClipHotkey) avec tous les autres (RegisterAllHotkeys). Un raccourci
        /// déjà pris par une autre application est signalé une seule fois.</summary>
        private void RegisterClipHotkey()
        {
            var text = _library.Settings.ClipHotkey;
            if (_hotkeys is null || !_library.Settings.ClipEnabled || string.IsNullOrEmpty(text)) return;
            if (!HotkeyParser.TryParse(text, out var modifiers, out var key)) return;

            if (_hotkeys.Register(modifiers, key, () => Dispatcher.Invoke(() => CaptureClip())) is null && _clipHotkeyWarnedFor != text)
            {
                _clipHotkeyWarnedFor = text;
                ToastService.Show(Loc.T("clip.toast_hotkey_taken", text), ToastKind.Warning);
            }
        }

        private void ClipButton_Click(object sender, RoutedEventArgs e) => CaptureClip();

        /// <summary>Garde les dernières secondes MAINTENANT (avant tout affichage : le contenu est celui de l'instant du clic),
        /// signale par un petit bip puis ouvre la fenêtre d'ajout sur le fichier.</summary>
        private async void CaptureClip()
        {
            if (!_library.Settings.ClipEnabled) return;
            if (_clipBusy)
            {
                ToastService.Show(Loc.T("clip.toast_busy"), ToastKind.Info);
                return;
            }

            var seconds = ClipSeconds;
            var snapshot = _clip?.Snapshot(seconds);
            if (snapshot is null)
            {
                ToastService.Show(Loc.T("clip.toast_not_running"), ToastKind.Warning);
                return;
            }
            if (PcmTools.PeakLevel(snapshot.Pcm) < ClipSilentPeak)
            {
                ToastService.Show(Loc.T("clip.toast_silent", seconds), ToastKind.Info);
                return;
            }

            _clipBusy = true;
            try
            {
                if (_library.Settings.ClipBeep) PlayBeep(BeepTone.ClipTones, 110);

                string path;
                try
                {
                    path = await Task.Run(() => SaveClipFile(snapshot));
                }
                catch (Exception ex)
                {
                    ToastService.Show(Loc.T("clip.toast_failed", ex.Message), ToastKind.Error);
                    return;
                }

                BringToFront();
                await AddSoundFiles(new[] { path }, Loc.T("clip.default_name", DateTime.Now), deleteFilesAfter: true);
            }
            finally
            {
                _clipBusy = false;
            }
        }

        private static string SaveClipFile(ClipSnapshot snapshot)
        {
            Directory.CreateDirectory(ClipsFolder);
            var path = Path.Combine(ClipsFolder, $"clip-{DateTime.Now:yyyyMMdd-HHmmss}.wav");
            PcmTools.WriteWavFile(path, snapshot.Pcm, snapshot.SampleRate, snapshot.Channels);
            return path;
        }

        /// <summary>Un clip est supprimé dès que la fenêtre d'ajout se ferme ; ceci ne rattrape que les restes d'une
        /// fermeture brutale (plantage, arrêt du PC) : les fichiers de plus d'un jour sont effacés au démarrage.</summary>
        private static void CleanOldClips()
        {
            try
            {
                if (!Directory.Exists(ClipsFolder)) return;
                foreach (var file in Directory.GetFiles(ClipsFolder, "clip-*.wav"))
                {
                    if (File.GetLastWriteTimeUtc(file) < DateTime.UtcNow.AddDays(-1)) File.Delete(file);
                }
            }
            catch
            {
                // Dossier temporaire verrouillé ou inaccessible : sans importance.
            }
        }

        private void BringToFront()
        {
            if (!IsVisible) Show();
            if (WindowState == WindowState.Minimized) WindowState = WindowState.Normal;
            Activate();
        }

        private static void TryDeleteFile(string path)
        {
            try { File.Delete(path); }
            catch { /* déjà supprimé ou encore ouvert : CleanOldClips s'en chargera */ }
        }
    }
}
