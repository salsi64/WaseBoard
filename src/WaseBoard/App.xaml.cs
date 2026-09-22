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
            // Une seule instance à la fois : plusieurs processus WaseBoard.exe ouverts en même
            // temps gardent chacun leur propre copie des réglages en mémoire, et le dernier à
            // sauvegarder écrase silencieusement les autres — c'est ce qui a fait disparaître des
            // catégories créées entre-temps dans une autre instance. On bascule vers la fenêtre
            // déjà ouverte plutôt que d'en lancer une seconde.
            //
            // IMPORTANT : ce contrôle doit se faire AVANT base.OnStartup(e), pas après — c'est
            // base.OnStartup qui traite StartupUri et crée/affiche MainWindow. L'appeler avant de
            // vérifier le verrou laissait une "deuxième" fenêtre se créer et charger/sauvegarder
            // des réglages (potentiellement vides, avant que Load() n'ait fini) avant même que le
            // Shutdown() ci-dessous ne prenne effet — c'est ce qui a écrasé settings.json (et sa
            // sauvegarde .bak) avec des valeurs par défaut lors du test précédent.
            _singleInstanceMutex = new Mutex(true, "WaseBoard_SingleInstance_Mutex", out var createdNew);
            if (!createdNew)
            {
                BringExistingInstanceToFront();
                Shutdown();
                return;
            }

            base.OnStartup(e);

            // Capture les exceptions non gérées pour éviter un crash silencieux. Reste une
            // MessageBox... non, une AlertDialog bloquante délibérément : l'app peut être dans un
            // état cassé à ce stade, un toast non garanti visible ne suffit pas.
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
                var hwnd = FindWindow(null, "WaseBoard");
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
