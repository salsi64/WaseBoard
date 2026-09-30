using System;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows;
using WaseBoard.Windows;

namespace WaseBoard
{
    public partial class App : Application
    {
        // Conservé pour toute la durée de vie du process : un Mutex non référencé serait
        // ramassé par le GC et libéré prématurément.
        private static Mutex? _singleInstanceMutex;

        protected override void OnStartup(StartupEventArgs e)
        {
            // Une seule instance à la fois (deux processus écrasent silencieusement les réglages
            // de l'autre) : on bascule vers la fenêtre déjà ouverte plutôt que d'en lancer une
            // seconde. Doit être vérifié AVANT base.OnStartup(e), qui crée/affiche MainWindow.
            _singleInstanceMutex = new Mutex(true, AppIdentity.MutexName, out var createdNew);
            if (!createdNew)
            {
                BringExistingInstanceToFront();
                Shutdown();
                return;
            }

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

        private static void BringExistingInstanceToFront()
        {
            try
            {
                var hwnd = FindWindow(null, AppIdentity.Name);
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

        private const int SW_RESTORE = 9;

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        private static extern IntPtr FindWindow(string? lpClassName, string lpWindowName);

        [DllImport("user32.dll")]
        private static extern bool SetForegroundWindow(IntPtr hWnd);

        [DllImport("user32.dll")]
        private static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    }
}
