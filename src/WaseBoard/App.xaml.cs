using System;
using System.Linq;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows;
using WaseBoard.Services.MicFx;
using WaseBoard.Windows;

namespace WaseBoard
{
    public partial class App : Application
    {
        // Conservé pour toute la durée de vie du process : un Mutex non référencé serait
        // ramassé par le GC et libéré prématurément.
        private static Mutex? _singleInstanceMutex;

        /// <summary>Lien waseboard:// reçu en argument de lancement (premier démarrage), à
        /// appliquer une fois MainWindow chargée — voir MainWindow.Window_Loaded.</summary>
        public static string? PendingDeepLink { get; private set; }

        protected override void OnStartup(StartupEventArgs e)
        {
            // Instance relancée en administrateur pour installer/retirer l'effet micro en jeu (voir
            // MicFxSetup) : aucune fenêtre ni mutex mono-instance, l'application normale attend sa fin.
            if (e.Args.Length > 0 && e.Args[0] == MicFxSetup.CommandLineSwitch)
            {
                Shutdown(MicFxSetup.RunCommandLine(e.Args));
                return;
            }

            var deepLink = e.Args.FirstOrDefault(a => a.StartsWith("waseboard://", StringComparison.OrdinalIgnoreCase));

            // Une seule instance à la fois (deux processus écrasent silencieusement les réglages
            // de l'autre) : on bascule vers la fenêtre déjà ouverte plutôt que d'en lancer une
            // seconde. Doit être vérifié AVANT base.OnStartup(e), qui crée/affiche MainWindow.
            _singleInstanceMutex = new Mutex(true, AppIdentity.MutexName, out var createdNew);
            if (!createdNew)
            {
                var hwnd = FindWindow(null, AppIdentity.Name);
                // Transmet le lien à l'instance déjà ouverte AVANT de la ramener au premier
                // plan/de quitter : sans ça, un clic sur un lien pendant que l'app tourne déjà
                // ne ferait rien (le processus qui portait l'argument s'arrête juste après).
                if (deepLink is not null && hwnd != IntPtr.Zero)
                    SendDeepLinkTo(hwnd, deepLink);
                BringExistingInstanceToFront(hwnd);
                Shutdown();
                return;
            }

            PendingDeepLink = deepLink;

            base.OnStartup(e);

            // Capture les exceptions non gérées : AlertDialog bloquante plutôt qu'un toast, l'app
            // pouvant être dans un état cassé à ce stade.
            DispatcherUnhandledException += (s, ex) =>
            {
                AlertDialog.Show(MainWindow,
                    "Une erreur est survenue :\n" + ex.Exception.Message,
                    "WaseBoard - Erreur", AlertKind.Error);
                ex.Handled = true;
            };
        }

        private static void BringExistingInstanceToFront(IntPtr hwnd)
        {
            try
            {
                if (hwnd == IntPtr.Zero) return;
                ShowWindow(hwnd, SW_RESTORE);
                SetForegroundWindow(hwnd);
            }
            catch
            {
                // Best-effort : si on ne retrouve pas la fenêtre existante, on quitte quand même
                // (mieux vaut ne rien montrer que risquer une seconde instance).
            }
        }

        /// <summary>Identifiant arbitraire (dwData) pour reconnaître nos propres messages
        /// WM_COPYDATA parmi d'éventuels autres envoyés à cette fenêtre.</summary>
        private const int DeepLinkMessageTag = 0x5742; // "WB"
        private const int WM_COPYDATA = 0x004A;

        private static void SendDeepLinkTo(IntPtr hwnd, string uri)
        {
            try
            {
                var bytes = System.Text.Encoding.Unicode.GetBytes(uri + "\0");
                var buffer = Marshal.AllocHGlobal(bytes.Length);
                try
                {
                    Marshal.Copy(bytes, 0, buffer, bytes.Length);
                    var cds = new COPYDATASTRUCT
                    {
                        dwData = (IntPtr)DeepLinkMessageTag,
                        cbData = bytes.Length,
                        lpData = buffer,
                    };
                    SendMessage(hwnd, WM_COPYDATA, IntPtr.Zero, ref cds);
                }
                finally
                {
                    Marshal.FreeHGlobal(buffer);
                }
            }
            catch
            {
                // Best-effort : au pire l'instance existante ne reçoit pas le lien, elle revient
                // juste au premier plan sans rien de plus (comportement d'avant cette fonctionnalité).
            }
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct COPYDATASTRUCT
        {
            public IntPtr dwData;
            public int cbData;
            public IntPtr lpData;
        }

        private const int SW_RESTORE = 9;

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        private static extern IntPtr FindWindow(string? lpClassName, string lpWindowName);

        [DllImport("user32.dll")]
        private static extern bool SetForegroundWindow(IntPtr hWnd);

        [DllImport("user32.dll")]
        private static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        private static extern IntPtr SendMessage(IntPtr hWnd, int Msg, IntPtr wParam, ref COPYDATASTRUCT lParam);
    }
}
