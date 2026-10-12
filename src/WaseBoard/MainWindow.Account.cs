using System.Windows.Media;
using WaseBoard.Services;
using WaseBoard.Services.Localization;

namespace WaseBoard
{
    // Compte Discord : état de l'application après une déconnexion volontaire (Paramètres › Discord).
    public partial class MainWindow
    {
        /// <summary>Après « Se déconnecter » : plus de session, donc plus de catalogue. On vide la page (au lieu de laisser des
        /// sons qui ne joueraient plus) et on l'explique ; la reconnexion se fait dans Paramètres › Discord.</summary>
        private void HandleLoggedOut()
        {
            Sounds.Clear();
            VoiceChannelMembers.Clear();
            NowPlayingEntries.Clear();
            RefreshSections();
            ServerStatusText.Text = Loc.T("status.logged_out");
            ServerStatusText.ToolTip = null;
            SetStatusDot(Color.FromRgb(0x8A, 0x8F, 0x98), pulsing: false);
            ToastService.Show(Loc.T("toast.logged_out"), ToastKind.Info);
        }
    }
}
