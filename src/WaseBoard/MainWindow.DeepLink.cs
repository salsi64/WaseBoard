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
using WaseBoard.Services.MicFx;
using WaseBoard.Windows;

namespace WaseBoard
{
    // Lien de connexion waseboard:// (instance déjà ouverte, premier lancement).
    public partial class MainWindow
    {
        // ---------- Lien de connexion waseboard:// ----------

        private const int WM_COPYDATA = 0x004A;
        // Doit correspondre à App.DeepLinkMessageTag — sert juste à reconnaître nos propres
        // messages WM_COPYDATA parmi d'éventuels autres envoyés à cette fenêtre.
        private const int DeepLinkMessageTag = 0x5742;

        [StructLayout(LayoutKind.Sequential)]
        private struct COPYDATASTRUCT
        {
            public IntPtr dwData;
            public int cbData;
            public IntPtr lpData;
        }

        /// <summary>Reçoit le lien envoyé par une seconde tentative de lancement (voir
        /// App.OnStartup/SendDeepLinkTo) pendant que cette instance est déjà ouverte.</summary>
        private IntPtr DeepLinkHwndHook(IntPtr hwnd, int msg, IntPtr wParam, IntPtr lParam, ref bool handled)
        {
            if (msg == WM_COPYDATA)
            {
                var cds = Marshal.PtrToStructure<COPYDATASTRUCT>(lParam);
                if (cds.dwData == (IntPtr)DeepLinkMessageTag)
                {
                    var uri = Marshal.PtrToStringUni(cds.lpData);
                    if (!string.IsNullOrEmpty(uri))
                        _ = ApplyDeepLinkAsync(uri, isRuntimeTrigger: true);
                    handled = true;
                }
            }
            return IntPtr.Zero;
        }

        /// <summary>Applique un lien waseboard://connect?url=...&amp;token=... — pré-remplit
        /// silencieusement si l'app n'était pas encore configurée (l'onboarding qui suit sert de
        /// confirmation visuelle), ou demande une confirmation explicite si ça change réellement de
        /// serveur (un tel lien peut en théorie être déclenché par n'importe quelle page/appli sur
        /// la machine). Renvoie true si de nouveaux réglages ont été appliqués (pour que l'appelant
        /// sache qu'un lien vient d'être traité — voir OnboardingReason.DeepLinkReconnect).</summary>
        private async Task<bool> ApplyDeepLinkAsync(string uri, bool isRuntimeTrigger)
        {
            // Une autre fenêtre modale (Paramètres, sélecteur de guilde à l'upload, découpe
            // audio...) est déjà ouverte : ne pas empiler une confirmation par-dessus, ce serait
            // visuellement confus et pourrait se perdre derrière la fenêtre active. On prévient
            // et on abandonne — l'utilisateur peut recliquer le lien une fois libre.
            if (Application.Current.Windows.Cast<Window>().Any(w => w != this && w.IsVisible))
            {
                ToastService.Show(
                    "Lien de connexion WaseBoard reçu — terminez d'abord l'action en cours, puis recliquez le lien.",
                    ToastKind.Warning);
                return false;
            }

            string? url = null, token = null;
            try
            {
                var parsed = new Uri(uri);
                foreach (var pair in parsed.Query.TrimStart('?').Split('&', StringSplitOptions.RemoveEmptyEntries))
                {
                    var kv = pair.Split('=', 2);
                    if (kv.Length != 2) continue;
                    if (kv[0] == "url") url = Uri.UnescapeDataString(kv[1]);
                    else if (kv[0] == "token") token = Uri.UnescapeDataString(kv[1]);
                }
            }
            catch { /* lien malformé : ignoré, pas une raison de planter */ }

            if (string.IsNullOrEmpty(url) || string.IsNullOrEmpty(token))
            {
                ToastService.Show("Lien de connexion WaseBoard invalide ou incomplet.", ToastKind.Warning);
                return false;
            }

            var isFreshInstall = string.IsNullOrEmpty(_library.Settings.ServerUrl) || !_library.Settings.HasSeenOnboarding;
            // Avertir uniquement si ça change RÉELLEMENT de serveur : recliquer le lien du même
            // serveur (cas courant, juste une reconnexion) ne doit pas afficher un avertissement
            // anxiogène pour rien.
            if (!isFreshInstall && url != _library.Settings.ServerUrl)
            {
                var confirmed = ConfirmDialog.Show(this,
                    $"Se connecter à un nouveau serveur WaseBoard ?\n\n{url}\n\nVos réglages de connexion actuels seront remplacés.");
                if (!confirmed) return false;

                // Les fichiers en cache appartiennent à l'ancien serveur — plus valides une fois
                // qu'on en change (et un ID de son pourrait en théorie se recouper entre deux
                // instances différentes).
                _library.ClearLocalCache();
            }

            _library.Settings.ServerUrl = url;
            _library.Settings.ServerToken = token;
            _library.Settings.DiscordSessionToken = null;
            _library.Settings.DiscordUserId = null;
            _library.Settings.DiscordUsername = null;
            _library.Settings.DiscordAvatarUrl = null;
            _library.SaveSettings();

            if (isRuntimeTrigger)
            {
                var onboarding = new OnboardingWindow(_library.Settings, _library, OnboardingReason.DeepLinkReconnect) { Owner = this };
                onboarding.ShowDialog();
                _library.Settings.HasSeenOnboarding = true;
                _library.SaveSettings();
                await RefreshCatalogAsync();
                await RefreshSharedCategoriesAsync();
                await RefreshVoiceStatusAsync();
            }
            // Sinon (reçu avant que Window_Loaded ait fini) : le bloc d'onboarding normal juste
            // après s'en charge déjà, pas besoin de dupliquer la logique ici.
            return true;
        }

        /// <summary>
        /// Statut vocal RÉEL, sondé en continu : contrairement à l'ancienne version (résolue une
        /// fois au démarrage à partir de la simple appartenance à un serveur Discord), celui-ci
        /// reflète votre présence vocale actuelle — jamais "connecté" si vous avez quitté le vocal,
        /// jamais le mauvais serveur si vous êtes ailleurs.
        /// </summary>
        private async Task RefreshVoiceStatusAsync()
        {
            if (string.IsNullOrEmpty(_library.Settings.DiscordSessionToken))
            {
                SidebarConnectionText.Text = "🔴 Non connecté à Discord";
                SidebarConnectionText.ToolTip = null;
                VoiceChannelMembers.Clear();
                return;
            }

            var (connected, channel, _, guildName, channelMembers, error) = await _library.GetLiveVoiceStatusAsync();
            var who = _library.Settings.DiscordUsername ?? "vous";

            // Une seule ligne dans la barre latérale (le détail complet est dans l'infobulle).
            if (error is not null)
            {
                SidebarConnectionText.Text = "⚠️ Statut indisponible";
                SidebarConnectionText.ToolTip = error;
            }
            else
            {
                SidebarConnectionText.Text = connected ? $"🟢 {guildName} · {channel}" : "⚪ Bot absent de votre vocal";
                SidebarConnectionText.ToolTip = connected
                    ? $"{who} — en vocal sur {guildName} ({channel})"
                    : $"{who} — le bot n'est pas dans votre salon vocal : cliquez sur « Vocal » pour qu'il vous rejoigne.";
            }

            var currentIds = VoiceChannelMembers.Select(m => m.UserId).ToHashSet();
            var newIds = channelMembers.Select(m => m.UserId).ToHashSet();

            foreach (var stale in VoiceChannelMembers.Where(m => !newIds.Contains(m.UserId)).ToList())
                VoiceChannelMembers.Remove(stale);
            foreach (var fresh in channelMembers.Where(m => !currentIds.Contains(m.UserId)))
                VoiceChannelMembers.Add(fresh);
        }

        private async void JoinVoiceButton_Click(object sender, RoutedEventArgs e)
        {
            JoinVoiceButton.IsEnabled = false;
            var (success, guildName, channelName, error) = await _library.JoinMyChannelAsync();
            JoinVoiceButton.IsEnabled = true;

            if (success)
            {
                await RefreshVoiceStatusAsync();
                ToastService.Show($"🔊 Connecté à « {channelName} » sur {guildName}.", ToastKind.Success);
            }
            else
            {
                ToastService.Show(error ?? "Échec de la connexion au salon vocal.", ToastKind.Warning);
            }
        }
    }
}
