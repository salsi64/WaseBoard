using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.ComponentModel;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Media.Effects;
using System.Windows.Threading;
using Microsoft.Win32;
using WaseBoard.Models;
using WaseBoard.Services;
using WaseBoard.Services.Localization;
using WaseBoard.Services.MicFx;
using WaseBoard.Windows;

namespace WaseBoard
{
    // Lecture audio : aperçu local, Discord, micro en jeu.
    public partial class MainWindow
    {
        // ---------- Lecture audio ----------

        /// <summary>
        /// Joue un son dans le vocal Discord. Le highlight s'allume localement dès le clic (sans
        /// attendre la moindre réponse réseau) ; le sondage régulier (PollActivityAsync) prend
        /// ensuite le relais pour refléter la réalité partagée (autres utilisateurs, extinction).
        /// En mode jeu (bouton 🎮 de la barre d'outils), le son part dans le micro, et dans Discord
        /// seulement si demandé (sinon un Discord branché sur ce même micro l'entendrait deux fois).
        /// </summary>
        private void Play(SoundItem item)
        {
            if (!IsGameMode)
            {
                _ = PlayAndPollAsync(item);
                return;
            }

            _ = PlayInMicAsync(item);
            if (_library.Settings.MicFeedAlsoDiscord) _ = PlayAndPollAsync(item);
        }

        private bool IsGameMode => _library.Settings.MicFeatureEnabled && _library.Settings.MicFeedEnabled;

        /// <summary>Interrupteur 🎮 Jeu | 🎧 Discord : un clic n'importe où bascule le mode.</summary>
        private void MicModeSwitch_Click(object sender, RoutedEventArgs e)
        {
            _library.Settings.MicFeedEnabled = !_library.Settings.MicFeedEnabled;
            _library.SaveSettings();
            ApplyMicModeButton();
        }

        // ----- Raccourci clavier global : bascule Jeu / Discord -----

        private string? _micHotkeyWarnedFor;
        private System.Media.SoundPlayer? _beepPlayer;

        /// <summary>Enregistre le raccourci de bascule Jeu/Discord (réglage MicModeHotkey) ; appelé avec tous les autres
        /// raccourcis (RegisterAllHotkeys). Sans effet si la fonction « micro en jeu » est désactivée ou si aucun raccourci
        /// n'est défini. Un raccourci déjà pris par une autre application est signalé une seule fois.</summary>
        private void RegisterMicModeHotkey()
        {
            var text = _library.Settings.MicModeHotkey;
            if (_hotkeys is null || !_library.Settings.MicFeatureEnabled || string.IsNullOrEmpty(text)) return;
            if (!HotkeyParser.TryParse(text, out var modifiers, out var key)) return;

            if (_hotkeys.Register(modifiers, key, () => Dispatcher.Invoke(ToggleMicModeFromHotkey)) is null && _micHotkeyWarnedFor != text)
            {
                _micHotkeyWarnedFor = text;
                ToastService.Show(Loc.T("toast.hotkey_taken", text), ToastKind.Warning);
            }
        }

        /// <summary>Bascule le mode comme l'interrupteur de la barre d'outils, puis le confirme par un message et, par défaut,
        /// un petit signal sonore : en plein jeu, la fenêtre WaseBoard n'est pas visible.</summary>
        private void ToggleMicModeFromHotkey()
        {
            if (!_library.Settings.MicFeatureEnabled) return;

            _library.Settings.MicFeedEnabled = !_library.Settings.MicFeedEnabled;
            _library.SaveSettings();
            ApplyMicModeButton();

            var game = IsGameMode;
            ToastService.Show(Loc.T(game ? "toast.mode_game" : "toast.mode_discord"), ToastKind.Info);
            if (_library.Settings.MicModeBeep) PlayModeBeep(game);
        }

        private void PlayModeBeep(bool game)
        {
            try
            {
                var wav = BeepTone.BuildWav(game ? BeepTone.RisingTones : BeepTone.FallingTones);
                _beepPlayer?.Dispose();
                _beepPlayer = new System.Media.SoundPlayer(new MemoryStream(wav));
                _beepPlayer.Play();
            }
            catch
            {
                // Pas de signal sonore possible (périphérique audio absent...) : le message à l'écran suffit.
            }
        }

        /// <summary>L'interrupteur n'existe que si la fonction est activée (Paramètres > Micro en jeu) ;
        /// le segment du mode actuel est coché, donc allumé, et seul lui porte son nom.</summary>
        private void ApplyMicModeButton()
        {
            MicModeSwitch.Visibility = _library.Settings.MicFeatureEnabled ? Visibility.Visible : Visibility.Collapsed;
            var game = IsGameMode;
            MicModeGameButton.IsChecked = game;
            MicModeDiscordButton.IsChecked = !game;
            MicModeSwitch.ToolTip = game
                ? "Mode jeu : les sons passent dans votre micro (chat vocal du jeu). Cliquer pour passer en mode Discord."
                : "Mode Discord : les sons sont joués par le bot dans votre salon vocal. Cliquer pour passer en mode jeu.";
            // La largeur de l'interrupteur change avec le mode (« Jeu » / « Discord ») : la barre se réajuste.
            ApplyToolbarDensity();
        }

        /// <summary>Seul le segment actif affiche son nom (l'autre se réduit à son icône), et aucun
        /// en barre étroite : l'interrupteur reste compact.</summary>
        private void UpdateMicModeLabels()
        {
            var game = IsGameMode;
            MicModeGameLabel.Visibility = game && !_toolbarCompact ? Visibility.Visible : Visibility.Collapsed;
            MicModeDiscordLabel.Visibility = !game && !_toolbarCompact ? Visibility.Visible : Visibility.Collapsed;
        }

        /// <summary>Micro en jeu : le son est ajouté au vrai micro par l'effet WaseBoardMicFx (toutes
        /// les applications qui l'écoutent l'entendent), plus le retour local si demandé. Le bouton
        /// reste allumé jusqu'à la fin réelle du son dans le micro (MicFeedService.SoundEnded).</summary>
        private async Task PlayInMicAsync(SoundItem item)
        {
            var localPath = await _library.GetOrDownloadCachedFileAsync(item);
            if (localPath is null)
            {
                ToastService.Show($"Impossible de jouer « {item.Name} » dans le micro : fichier indisponible.", ToastKind.Error);
                return;
            }

            TimeSpan? trimStart = item.TrimStartMs is { } startMs ? TimeSpan.FromMilliseconds(startMs) : null;
            TimeSpan? trimEnd = item.TrimEndMs is { } endMs ? TimeSpan.FromMilliseconds(endMs) : null;
            try
            {
                _micFeed.Volume = _library.Settings.MicFeedVolume;
                _micFeed.Play(item.Id, localPath, item.Volume, trimStart, trimEnd);
            }
            catch (Exception ex)
            {
                ToastService.Show($"Impossible de jouer « {item.Name} » dans le micro : {ex.Message}", ToastKind.Error);
                return;
            }

            _micPlayingIds.Add(item.Id);
            item.IsPlaying = true;

            if (_library.Settings.MicFeedMonitor) await PlayLocalAsync(item);
        }

        private async Task PlayAndPollAsync(SoundItem item)
        {
            item.IsPlaying = true; // optimiste : corrigé par le prochain sondage si le serveur dit le contraire
            var ok = await _library.PlayOnServerAsync(item.Id, item.Volume);
            if (!ok)
            {
                item.IsPlaying = false; // rien n'a réellement démarré, pas la peine d'attendre le sondage
                ToastService.Show(
                    $"Impossible de jouer « {item.Name} »." + (string.IsNullOrEmpty(_library.LastErrorDetail) ? "" : " " + _library.LastErrorDetail),
                    ToastKind.Error);
                return;
            }

            // À cet instant, le son est déjà en train de jouer côté serveur (mixer.add() s'exécute
            // avant la réponse HTTP, voir _handle_play) : plutôt que d'attendre qu'un sondage
            // constate la fin réelle (jusqu'à 150ms de retard), on éteint localement dès la durée
            // effective écoulée. Le sondage régulier reste la source de vérité si quelqu'un
            // d'autre joue encore ce même son (il rallume dans ce cas).
            var duration = GetEffectivePlaybackDuration(item);
            if (duration > TimeSpan.Zero) _ = ScheduleLocalStopAsync(item, duration);

            // PAS de sondage immédiat ici : côté serveur, l'enregistrement de l'activité (avatar,
            // qui joue quoi) se fait dans une tâche asynchrone séparée, lancée APRÈS la réponse à
            // /play (voir _record_play_activity) pour ne jamais retarder le son lui-même. Sonder
            // tout de suite la voit donc souvent encore vide et écraserait le IsPlaying optimiste
            // ci-dessus avec false, avant que le prochain tick du minuteur régulier (150ms) ne le
            // remette à true — un allumage/extinction/rallumage bien visible. Le minuteur régulier
            // se chargera de refléter l'activité réelle (avatar compris) sans cette course.
        }

        private static TimeSpan GetEffectivePlaybackDuration(SoundItem item)
        {
            var startMs = item.TrimStartMs ?? 0;
            var endMs = item.TrimEndMs ?? item.DurationMs;
            var ms = endMs - startMs;
            return ms > 0 ? TimeSpan.FromMilliseconds(ms) : TimeSpan.Zero;
        }

        private static async Task ScheduleLocalStopAsync(SoundItem item, TimeSpan duration)
        {
            await Task.Delay(duration);
            if (item.IsPlaying) item.IsPlaying = false;
        }

        /// <summary>Aperçu : lecture locale uniquement (icône mégaphone), jamais envoyée au serveur/bot Discord.</summary>
        private async void PreviewLocal(SoundItem item) => await PlayLocalAsync(item);

        private async Task PlayLocalAsync(SoundItem item)
        {
            var localPath = await _library.GetOrDownloadCachedFileAsync(item);
            if (localPath is null) return;

            var defaultDevice = AudioPlaybackService.GetDefaultOutputDevice();
            if (defaultDevice is null) return;

            var volume = item.Volume * _library.Settings.LocalPlaybackVolume;
            // Le fichier en cache est le son COMPLET : un son découpé ne joue que sa portion gardée, comme sur le serveur.
            TimeSpan? trimStart = item.TrimStartMs is { } startMs ? TimeSpan.FromMilliseconds(startMs) : null;
            TimeSpan? trimEnd = item.TrimEndMs is { } endMs ? TimeSpan.FromMilliseconds(endMs) : null;
            _audio.PlaySound(item.Id, localPath, new[] { new AudioPlaybackService.PlaybackTarget(defaultDevice, volume) }, trimStart, trimEnd);
        }

        /// <summary>Coupe la lecture locale ET tous les sons actuellement mixés côté serveur (dans le salon où vous êtes).</summary>
        private async void StopAllButton_Click(object sender, RoutedEventArgs e)
        {
            _audio.StopAll();
            _micFeed.StopAll();
            var ok = await _library.StopAllOnServerAsync();
            if (!ok)
            {
                ToastService.Show(
                    "Impossible de couper les sons côté serveur." +
                    (string.IsNullOrEmpty(_library.LastErrorDetail) ? "" : " " + _library.LastErrorDetail),
                    ToastKind.Warning);
            }
        }
    }
}
