using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Text.Json;
using System.Threading.Tasks;
using Microsoft.Win32;
using NAudio.CoreAudioApi;

namespace WaseBoard.Services.MicFx
{
    /// <summary>Installation de l'effet micro (APO WaseBoardMicFx) : copie de la DLL dans Program Files,
    /// inscription COM/moteur audio, et ajout de l'effet EN FIN de la liste d'effets d'endpoint
    /// composite de chaque micro choisi — les effets du fabricant restent en place, rien n'est
    /// remplacé. Tout ce qui est modifié est noté sous HKLM\SOFTWARE\WaseBoard\MicFx pour être
    /// défait à l'identique.
    ///
    /// Les écritures demandent les droits administrateur : elles s'exécutent dans une instance de
    /// WaseBoard relancée élevée (« --micfx … », voir App.OnStartup), jamais dans l'application
    /// normale, qui ne fait que lire l'état.</summary>
    public static class MicFxSetup
    {
        public const string ClsidString = "{8C9DCFA9-29AB-4056-936F-6721FBD44AEC}";
        public const string CommandLineSwitch = "--micfx";
        private const string DllFileName = "WaseBoardMicFx.dll";

        private const string CaptureRoot = @"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Capture";
        private const string BackupRoot = @"SOFTWARE\WaseBoard\MicFx\Endpoints";
        private const string ClsidKeyPath = @"SOFTWARE\Classes\CLSID\" + ClsidString;
        private const string ApoKeyPath = @"SOFTWARE\Classes\AudioEngine\AudioProcessingObjects\" + ClsidString;

        // Clés de propriétés des effets (audioenginebaseapo.h, mmdeviceapi.h).
        private const string PkeyCompositeEndpointEffect = "{d04e05a6-594b-4fb6-a80d-01af5eed7d1d},15";
        private const string PkeyEndpointEffect = "{d04e05a6-594b-4fb6-a80d-01af5eed7d1d},7";
        private const string PkeyEfxModesForStreaming = "{d3993a3f-99c2-4402-b5ec-a92a0367664b},7";
        private const string PkeyDisableSysFx = "{1da5d803-d492-4edd-8c23-e0c0ffee7f0e},5";
        private const string ModeDefault = "{C18E2F7E-933D-4965-B7D1-1EEF228D2AF3}";
        private const string IidAudioProcessingObject = "{FD7F2B29-24D0-4B5C-B177-592C39F9CA10}";

        /// <summary>Dossier lisible par audiodg.exe (LOCAL SERVICE) et non modifiable sans droits admin :
        /// jamais le profil utilisateur, sinon n'importe quel programme pourrait remplacer la DLL
        /// chargée par le moteur audio.</summary>
        private static string InstallDir =>
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "WaseBoard", "MicFx");

        /// <summary>DLL livrée à côté de WaseBoard.exe (absente d'une build qui ne l'embarque pas).</summary>
        public static string BundledDllPath => Path.Combine(AppContext.BaseDirectory, DllFileName);

        public static bool IsBundled => File.Exists(BundledDllPath);

        /// <summary>Nom d'installation de la DLL livrée (empreinte de son contenu), calculé une seule fois :
        /// ReadState est appelé toutes les quelques secondes en mode jeu.</summary>
        private static readonly Lazy<string?> BundledInstalledFileName =
            new(() => IsBundled ? InstalledFileName(BundledDllPath) : null);

        // ---------- Lecture de l'état (sans droits admin) ----------

        public sealed record MicInfo(string EndpointGuid, string Name, bool IsDefaultCommunications,
            bool Compatible, bool Equipped, bool NeedsRepair, bool EnhancementsDisabled);

        /// <summary>NeedsUpdate : l'effet installé n'est pas celui livré avec cette version de WaseBoard
        /// (les fichiers installés portent l'empreinte de leur contenu) — à réinstaller.</summary>
        public sealed record State(bool Registered, bool NeedsUpdate, IReadOnlyList<MicInfo> Microphones)
        {
            public bool AnyEquipped => Microphones.Any(m => m.Equipped);
            public bool AnyNeedsRepair => Microphones.Any(m => m.NeedsRepair);
        }

        public static State ReadState()
        {
            var registered = false;
            var needsUpdate = false;
            using (var inproc = Registry.LocalMachine.OpenSubKey(ClsidKeyPath + @"\InprocServer32"))
            {
                if (inproc?.GetValue("") is string path && File.Exists(path))
                {
                    registered = true;
                    needsUpdate = BundledInstalledFileName.Value is { } bundled
                        && !string.Equals(Path.GetFileName(path), bundled, StringComparison.OrdinalIgnoreCase);
                }
            }

            var mics = new List<MicInfo>();
            try
            {
                using var enumerator = new MMDeviceEnumerator();
                string? defaultId = null;
                try { defaultId = enumerator.GetDefaultAudioEndpoint(DataFlow.Capture, Role.Communications).ID; }
                catch (Exception) { /* aucun micro par défaut */ }

                foreach (var device in enumerator.EnumerateAudioEndPoints(DataFlow.Capture, DeviceState.Active))
                {
                    var guid = EndpointGuidFromId(device.ID);
                    if (guid is null) continue;
                    mics.Add(ReadMic(guid, device.FriendlyName, device.ID == defaultId));
                }
            }
            catch (Exception)
            {
                // Énumération impossible (service audio arrêté) : liste vide, l'interface l'affiche.
            }

            return new State(registered, needsUpdate, mics);
        }

        private static MicInfo ReadMic(string guid, string name, bool isDefault)
        {
            using var fx = Registry.LocalMachine.OpenSubKey($@"{CaptureRoot}\{guid}\FxProperties");
            using var props = Registry.LocalMachine.OpenSubKey($@"{CaptureRoot}\{guid}\Properties");
            using var backup = Registry.LocalMachine.OpenSubKey($@"{BackupRoot}\{guid}");

            var equipped = fx?.GetValue(PkeyCompositeEndpointEffect) is string[] list && ContainsOurs(list);
            var enhancementsDisabled = props?.GetValue(PkeyDisableSysFx) is int disabled && disabled != 0;
            var installedByUs = backup is not null;

            return new MicInfo(guid, name, isDefault,
                Compatible: fx is not null,
                Equipped: equipped,
                NeedsRepair: installedByUs && (!equipped || enhancementsDisabled),
                EnhancementsDisabled: enhancementsDisabled);
        }

        /// <summary>« {0.0.1.00000000}.{guid} » → « {guid} » (nom de la clé sous MMDevices\Audio\Capture).</summary>
        private static string? EndpointGuidFromId(string endpointId)
        {
            var dot = endpointId.LastIndexOf('.');
            var guid = dot >= 0 ? endpointId[(dot + 1)..] : endpointId;
            return guid.StartsWith('{') && guid.EndsWith('}') ? guid : null;
        }

        private static bool ContainsOurs(IEnumerable<string> clsids) =>
            clsids.Any(c => string.Equals(c, ClsidString, StringComparison.OrdinalIgnoreCase));

        // ---------- Lancement élevé (depuis l'application normale) ----------

        public sealed record Result(bool Ok, bool Cancelled, List<string> Messages);

        /// <summary>Installe (ou répare) l'effet sur les micros donnés, via une instance élevée (invite UAC).</summary>
        public static Task<Result> InstallAsync(IEnumerable<string> endpointGuids) =>
            RunElevatedAsync("install", "--source", BundledDllPath, "--endpoints", string.Join(",", endpointGuids));

        /// <summary>Retire l'effet de tous les micros et désinscrit la DLL, via une instance élevée.</summary>
        public static Task<Result> UninstallAsync() => RunElevatedAsync("uninstall");

        private static async Task<Result> RunElevatedAsync(params string[] args)
        {
            var resultPath = Path.Combine(Path.GetTempPath(), $"waseboard-micfx-{Guid.NewGuid():N}.json");
            var arguments = string.Join(" ",
                new[] { CommandLineSwitch }.Concat(args).Concat(new[] { "--result", resultPath }).Select(Quote));

            var psi = new ProcessStartInfo(Environment.ProcessPath ?? throw new InvalidOperationException("Chemin de WaseBoard introuvable."))
            {
                Arguments = arguments,
                UseShellExecute = true,
                Verb = "runas",
                WindowStyle = ProcessWindowStyle.Hidden,
            };

            try
            {
                using var process = Process.Start(psi);
                if (process is null) return new Result(false, false, new() { "Impossible de lancer l'installation." });
                await process.WaitForExitAsync();
            }
            catch (Win32Exception ex) when (ex.NativeErrorCode == 1223) // ERROR_CANCELLED : UAC refusé
            {
                return new Result(false, true, new() { "Installation annulée (droits administrateur refusés)." });
            }

            try
            {
                var json = await File.ReadAllTextAsync(resultPath);
                return JsonSerializer.Deserialize<Result>(json) ?? new Result(false, false, new() { "Résultat illisible." });
            }
            catch (Exception ex)
            {
                return new Result(false, false, new() { "L'installation n'a pas rendu de résultat : " + ex.Message });
            }
            finally
            {
                try { File.Delete(resultPath); } catch { /* fichier temporaire, sans importance */ }
            }
        }

        private static string Quote(string arg) => "\"" + arg.Replace("\"", "\\\"") + "\"";

        // ---------- Exécution élevée (instance « --micfx ») ----------

        /// <summary>Point d'entrée de l'instance élevée. Renvoie le code de sortie du processus.</summary>
        public static int RunCommandLine(string[] args)
        {
            string? Option(string name)
            {
                var i = Array.IndexOf(args, name);
                return i >= 0 && i + 1 < args.Length ? args[i + 1] : null;
            }

            var command = args.Length > 1 ? args[1] : "";
            var resultPath = Option("--result");
            var messages = new List<string>();
            var ok = false;

            try
            {
                switch (command)
                {
                    case "install":
                        var source = Option("--source") ?? BundledDllPath;
                        var endpoints = (Option("--endpoints") ?? "")
                            .Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
                        ok = InstallElevated(source, endpoints, messages);
                        break;
                    case "uninstall":
                        ok = UninstallElevated(messages);
                        break;
                    default:
                        messages.Add($"Commande inconnue : « {command} ».");
                        break;
                }
            }
            catch (Exception ex)
            {
                messages.Add("Erreur : " + ex.Message);
                ok = false;
            }

            if (resultPath is not null)
            {
                try { File.WriteAllText(resultPath, JsonSerializer.Serialize(new Result(ok, false, messages))); }
                catch { /* l'appelant signalera l'absence de résultat */ }
            }
            return ok ? 0 : 1;
        }

        private static bool InstallElevated(string sourceDll, IReadOnlyList<string> endpointGuids, List<string> messages)
        {
            if (!File.Exists(sourceDll))
            {
                messages.Add("Fichier WaseBoardMicFx.dll introuvable à côté de WaseBoard : cette version ne contient pas le module micro.");
                return false;
            }
            if (endpointGuids.Count == 0)
            {
                messages.Add("Aucun micro sélectionné.");
                return false;
            }

            var dllPath = CopyDll(sourceDll);
            RegisterCom(dllPath);

            var equipped = 0;
            foreach (var guid in endpointGuids)
            {
                if (EquipEndpoint(guid, messages)) equipped++;
            }

            RestartAudioService(messages);
            DeleteOldDlls(keep: dllPath); // après le redémarrage : l'ancienne version n'est plus chargée
            if (equipped > 0) messages.Insert(0, equipped == 1 ? "Effet installé sur 1 micro." : $"Effet installé sur {equipped} micros.");
            return equipped > 0;
        }

        private static bool UninstallElevated(List<string> messages)
        {
            var guids = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            using (var backups = Registry.LocalMachine.OpenSubKey(BackupRoot))
            {
                if (backups is not null) guids.UnionWith(backups.GetSubKeyNames());
            }
            // Aussi les micros équipés sans sauvegarde (installation interrompue, sauvegarde effacée…).
            using (var capture = Registry.LocalMachine.OpenSubKey(CaptureRoot))
            {
                foreach (var guid in capture?.GetSubKeyNames() ?? Array.Empty<string>())
                {
                    using var fx = capture!.OpenSubKey($@"{guid}\FxProperties");
                    if (fx?.GetValue(PkeyCompositeEndpointEffect) is string[] list && ContainsOurs(list)) guids.Add(guid);
                }
            }

            foreach (var guid in guids) UnequipEndpoint(guid, messages);

            Registry.LocalMachine.DeleteSubKeyTree(ClsidKeyPath, throwOnMissingSubKey: false);
            Registry.LocalMachine.DeleteSubKeyTree(ApoKeyPath, throwOnMissingSubKey: false);
            Registry.LocalMachine.DeleteSubKeyTree(@"SOFTWARE\WaseBoard\MicFx", throwOnMissingSubKey: false);

            RestartAudioService(messages);
            DeleteOldDlls(keep: null);
            DeleteIfEmpty(InstallDir);
            DeleteIfEmpty(Path.GetDirectoryName(InstallDir)!); // Program Files\WaseBoard, sauf si WaseBoard y est installé
            messages.Insert(0, "Effet micro retiré.");
            return true;
        }

        /// <summary>Copie versionnée par contenu : une DLL déjà chargée par audiodg ne peut pas être
        /// écrasée, une mise à jour s'installe donc à côté et l'inscription bascule dessus.</summary>
        private static string CopyDll(string sourceDll)
        {
            Directory.CreateDirectory(InstallDir);
            var target = Path.Combine(InstallDir, InstalledFileName(sourceDll));
            if (!File.Exists(target)) File.Copy(sourceDll, target);
            return target;
        }

        /// <summary>« WaseBoardMicFx-&lt;empreinte du contenu&gt;.dll » : nom sous lequel cette DLL est installée.</summary>
        private static string InstalledFileName(string dllPath)
        {
            using var stream = File.OpenRead(dllPath);
            return $"WaseBoardMicFx-{Convert.ToHexString(SHA256.HashData(stream))[..12].ToLowerInvariant()}.dll";
        }

        private static void DeleteOldDlls(string? keep)
        {
            if (!Directory.Exists(InstallDir)) return;
            foreach (var file in Directory.GetFiles(InstallDir, "WaseBoardMicFx-*.dll"))
            {
                if (keep is not null && string.Equals(file, keep, StringComparison.OrdinalIgnoreCase)) continue;
                try { File.Delete(file); }
                catch { /* encore chargée par audiodg : sera retirée à la prochaine installation */ }
            }
        }

        private static void DeleteIfEmpty(string directory)
        {
            try
            {
                if (Directory.Exists(directory) && !Directory.EnumerateFileSystemEntries(directory).Any())
                    Directory.Delete(directory);
            }
            catch { /* dossier vide laissé en place, sans conséquence */ }
        }

        private static void RegisterCom(string dllPath)
        {
            using (var clsid = Registry.LocalMachine.CreateSubKey(ClsidKeyPath))
            {
                clsid.SetValue("", "WaseBoard Mic Effect");
                using var inproc = clsid.CreateSubKey("InprocServer32");
                inproc.SetValue("", dllPath);
                inproc.SetValue("ThreadingModel", "Both");
            }

            // Mêmes valeurs que APO_REG_PROPERTIES dans la DLL (MakeRegProperties).
            using var apo = Registry.LocalMachine.CreateSubKey(ApoKeyPath);
            apo.SetValue("FriendlyName", "WaseBoard Mic Effect");
            apo.SetValue("Copyright", "WaseBoard (licence MIT)");
            apo.SetValue("MajorVersion", 1, RegistryValueKind.DWord);
            apo.SetValue("MinorVersion", 0, RegistryValueKind.DWord);
            apo.SetValue("Flags", 0xE, RegistryValueKind.DWord); // APO_FLAG_DEFAULT
            apo.SetValue("MinInputConnections", 1, RegistryValueKind.DWord);
            apo.SetValue("MaxInputConnections", 1, RegistryValueKind.DWord);
            apo.SetValue("MinOutputConnections", 1, RegistryValueKind.DWord);
            apo.SetValue("MaxOutputConnections", 1, RegistryValueKind.DWord);
            apo.SetValue("MaxInstances", unchecked((int)0xFFFFFFFF), RegistryValueKind.DWord);
            apo.SetValue("NumAPOInterfaces", 1, RegistryValueKind.DWord);
            apo.SetValue("APOInterface0", IidAudioProcessingObject);
        }

        /// <summary>Les clés MMDevices n'accordent aux administrateurs que « Lire » et « Définir la
        /// valeur » : on ouvre donc avec exactement ces droits (un OpenSubKey(writable: true) demande
        /// aussi la création de sous-clés et serait refusé).</summary>
        private static RegistryKey? OpenForValues(string path) =>
            Registry.LocalMachine.OpenSubKey(path, RegistryKeyPermissionCheck.ReadWriteSubTree,
                RegistryRights.ReadKey | RegistryRights.SetValue);

        private static bool EquipEndpoint(string guid, List<string> messages)
        {
            var name = MicName(guid);
            using var fx = OpenForValues($@"{CaptureRoot}\{guid}\FxProperties");
            if (fx is null)
            {
                messages.Add($"« {name} » : ce micro n'accepte pas d'effets audio (pilote sans section d'effets).");
                return false;
            }

            var composite = fx.GetValue(PkeyCompositeEndpointEffect) as string[];
            var single = fx.GetValue(PkeyEndpointEffect) as string;
            var modes = fx.GetValue(PkeyEfxModesForStreaming) as string[];

            using var backup = Registry.LocalMachine.CreateSubKey($@"{BackupRoot}\{guid}");
            if (backup.GetValue("Saved") is null)
            {
                backup.SetValue("HadComposite", composite is null ? 0 : 1, RegistryValueKind.DWord);
                backup.SetValue("HadModes", modes is null ? 0 : 1, RegistryValueKind.DWord);
                backup.SetValue("Saved", 1, RegistryValueKind.DWord);
            }

            // Liste composite = effets du fabricant + le nôtre en dernier. Si le pilote n'utilise que
            // l'ancienne clé simple, on la reprend en tête de liste pour ne pas l'écarter.
            var list = composite?.ToList()
                ?? (string.IsNullOrEmpty(single) ? new List<string>() : new List<string> { single });
            if (!ContainsOurs(list)) list.Add(ClsidString);
            fx.SetValue(PkeyCompositeEndpointEffect, list.ToArray(), RegistryValueKind.MultiString);

            // Sans mode de streaming déclaré, un effet d'endpoint n'est que « découvert », jamais
            // inséré dans le flux. Seul DEFAULT est permis pour un EFX.
            if (modes is null || modes.Length == 0)
                fx.SetValue(PkeyEfxModesForStreaming, new[] { ModeDefault }, RegistryValueKind.MultiString);

            // « Améliorations audio » désactivées = aucun effet chargé, le nôtre compris.
            using (var props = OpenForValues($@"{CaptureRoot}\{guid}\Properties"))
            {
                if (props?.GetValue(PkeyDisableSysFx) is int disabled && disabled != 0)
                {
                    if (backup.GetValue("OriginalDisableSysFx") is null)
                        backup.SetValue("OriginalDisableSysFx", disabled, RegistryValueKind.DWord);
                    props.SetValue(PkeyDisableSysFx, 0, RegistryValueKind.DWord);
                }
            }

            messages.Add($"« {name} » : effet ajouté.");
            return true;
        }

        private static void UnequipEndpoint(string guid, List<string> messages)
        {
            var name = MicName(guid);
            using var backup = Registry.LocalMachine.OpenSubKey($@"{BackupRoot}\{guid}");
            var hadComposite = backup?.GetValue("HadComposite") is not int c || c != 0;
            var hadModes = backup?.GetValue("HadModes") is not int m || m != 0;

            using (var fx = OpenForValues($@"{CaptureRoot}\{guid}\FxProperties"))
            {
                if (fx?.GetValue(PkeyCompositeEndpointEffect) is string[] list)
                {
                    var remaining = list.Where(x => !string.Equals(x, ClsidString, StringComparison.OrdinalIgnoreCase)).ToArray();
                    var single = fx.GetValue(PkeyEndpointEffect) as string;
                    var onlyCopiedSingle = remaining.Length == 0
                        || (remaining.Length == 1 && string.Equals(remaining[0], single, StringComparison.OrdinalIgnoreCase));

                    if (!hadComposite && onlyCopiedSingle) fx.DeleteValue(PkeyCompositeEndpointEffect, throwOnMissingValue: false);
                    else fx.SetValue(PkeyCompositeEndpointEffect, remaining, RegistryValueKind.MultiString);

                    if (!hadModes) fx.DeleteValue(PkeyEfxModesForStreaming, throwOnMissingValue: false);
                }
            }

            if (backup?.GetValue("OriginalDisableSysFx") is int original)
            {
                using var props = OpenForValues($@"{CaptureRoot}\{guid}\Properties");
                props?.SetValue(PkeyDisableSysFx, original, RegistryValueKind.DWord);
            }

            messages.Add($"« {name} » : effet retiré.");
        }

        private static string MicName(string guid)
        {
            using var props = Registry.LocalMachine.OpenSubKey($@"{CaptureRoot}\{guid}\Properties");
            // PKEY_Device_FriendlyName
            return props?.GetValue("{a45c254e-df1c-4efd-8020-67d146a850e0},2") as string ?? guid;
        }

        /// <summary>Les propriétés d'effets des micros sont gardées en mémoire par le service
        /// « Générateur de points de terminaison audio » : le redémarrer (avec le service audio qui
        /// en dépend, coupure du son ~2 s) rend la nouvelle liste d'effets effective sans redémarrer Windows.</summary>
        private static void RestartAudioService(List<string> messages)
        {
            var net = Path.Combine(Environment.SystemDirectory, "net.exe");
            var stopped = RunHidden(net, "stop AudioEndpointBuilder /y");
            var started = RunHidden(net, "start AudioEndpointBuilder") & RunHidden(net, "start Audiosrv");
            if (!stopped || !started)
                messages.Add("Le service audio n'a pas pu être redémarré : redémarrez Windows pour finaliser.");
        }

        private static bool RunHidden(string fileName, string arguments)
        {
            try
            {
                using var process = Process.Start(new ProcessStartInfo(fileName, arguments)
                {
                    UseShellExecute = false,
                    CreateNoWindow = true,
                });
                if (process is null) return false;
                if (!process.WaitForExit(30_000)) return false;
                // net start renvoie 2 si le service tourne déjà : pas une erreur ici.
                return process.ExitCode is 0 or 2;
            }
            catch (Exception)
            {
                return false;
            }
        }
    }
}
