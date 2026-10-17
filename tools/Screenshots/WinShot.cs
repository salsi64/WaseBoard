using System.Linq;
using System;
using System.IO;
using System.Reflection;
using System.Threading;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;

static class WinShot
{
    static void Pump(int ms)
    {
        var end = DateTime.Now.AddMilliseconds(ms);
        while (DateTime.Now < end) { Dispatcher.CurrentDispatcher.Invoke(() => { }, DispatcherPriority.Background); Thread.Sleep(20); }
    }

    public static void Run(string outPng, string theme, string which)
    {
        var app = new WaseBoard.App();
        app.InitializeComponent();
        typeof(Application).GetField("_startupUri", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance)?.SetValue(app, null); // évite la 2e MainWindow (StartupUri) ouverte par le Pump

        app.ShutdownMode = ShutdownMode.OnExplicitShutdown;

        typeof(WaseBoard.MainWindow).GetMethod("ApplyThemeTokens", BindingFlags.NonPublic | BindingFlags.Static)!
            .Invoke(null, new object[] { theme == "Flat" });

        var settings = new WaseBoard.Models.AppSettings { UiTheme = theme };
        var library = new WaseBoard.Services.SoundLibraryService();
        Window w = which switch
        {
            "settings" => new WaseBoard.Windows.SettingsWindow(settings, library, "Appearance"),
            "diag" => Diag(settings, library),
            "discord" => DiscordPage(settings, library),
            "micpage" => MicPage(settings, library),
            "clippage" => ClipPage(settings, library),
            "cliptrim" => ClipTrim(),
            "onboarding" => new WaseBoard.Windows.OnboardingWindow(settings, library, WaseBoard.Windows.OnboardingReason.FreshInstall),
            "confirm" => new WaseBoard.Windows.ConfirmDialog("Supprimer la catégorie « Mèmes » ?", "Confirmer"),
            "prompt" => new WaseBoard.Windows.PromptDialog("Nouveau nom de catégorie :", "Mèmes"),
            "alert" => new WaseBoard.Windows.AlertDialog("Impossible de charger le son complet.", "WaseBoard", WaseBoard.Windows.AlertKind.Warning),
            "emoji" => new WaseBoard.Windows.EmojiPickerWindow("🔥"),
            "hotkey" => new WaseBoard.Windows.HotkeyCaptureWindow(),
            "glyphs" => Glyphs(),
            "edit" => EditWindow(true),
            "editlocal" => EditWindow(false),
            "siteedit" => SiteEdit(),
            _ => throw new ArgumentException(which)
        };
        w.WindowStartupLocation = WindowStartupLocation.Manual; w.Left = 80; w.Top = 40;
        Console.WriteLine("before-show windows=" + Application.Current.Windows.Count);
        w.Show();
        Pump(which == "diag" ? 5000 : 1200);
        if (which == "siteedit" && w.FindName("ConfirmButton") is System.Windows.Controls.Button cb) { cb.IsEnabled = true; Pump(300); }
        Console.WriteLine("after-pump Field=" + ((System.Windows.Media.SolidColorBrush)Application.Current.Resources["FieldBrush"]).Color + " windows=" + Application.Current.Windows.Count + " types=" + string.Join(",", System.Linq.Enumerable.Select(System.Linq.Enumerable.Cast<Window>(Application.Current.Windows), x => x.GetType().Name)));
        if (which == "micpage" || which == "clippage") { w.UpdateLayout(); ScrollAll(w); Pump(300); }
        w.UpdateLayout();
        var scale = double.Parse(Environment.GetEnvironmentVariable("SHOT_SCALE") ?? "1.5", System.Globalization.CultureInfo.InvariantCulture);
        var rtb = new RenderTargetBitmap((int)(w.ActualWidth * scale), (int)(w.ActualHeight * scale), 96 * scale, 96 * scale, PixelFormats.Pbgra32);
        rtb.Render(w);
        var enc = new PngBitmapEncoder();
        enc.Frames.Add(BitmapFrame.Create(rtb));
        using (var fs = File.Create(outPng)) enc.Save(fs);
        Console.WriteLine($"WIN OK {which}/{theme} {w.ActualWidth}x{w.ActualHeight}");
        w.Close();
        app.Shutdown();
    }

    static void ScrollAll(System.Windows.DependencyObject d)
    {
        for (int i = 0; i < System.Windows.Media.VisualTreeHelper.GetChildrenCount(d); i++)
        {
            var c = System.Windows.Media.VisualTreeHelper.GetChild(d, i);
            if (c is System.Windows.Controls.ScrollViewer sv) sv.ScrollToEnd();
            ScrollAll(c);
        }
    }

    static Window Glyphs()
    {
        var panel = new System.Windows.Controls.WrapPanel { Margin = new Thickness(10), Width = 560 };
        foreach (var g in new[] { "E8B7", "E8F4", "E8F1", "E8D2", "E8A9", "E81C", "E71D", "E8EC", "E7C3", "E8FD", "E7B8", "E70B" })
        {
            var sp = new System.Windows.Controls.StackPanel { Margin = new Thickness(8), Width = 70 };
            sp.Children.Add(new System.Windows.Controls.TextBlock { Text = char.ConvertFromUtf32(Convert.ToInt32(g, 16)), FontFamily = new FontFamily("Segoe MDL2 Assets"), FontSize = 26, Foreground = Brushes.White, HorizontalAlignment = HorizontalAlignment.Center });
            sp.Children.Add(new System.Windows.Controls.TextBlock { Text = g, Foreground = Brushes.Gray, HorizontalAlignment = HorizontalAlignment.Center });
            panel.Children.Add(sp);
        }
        return new Window { Content = panel, Background = Brushes.Black, SizeToContent = SizeToContent.WidthAndHeight, WindowStyle = WindowStyle.None };
    }

    static Window DiscordPage(WaseBoard.Models.AppSettings settings, WaseBoard.Services.SoundLibraryService library)
    {
        settings.DiscordSessionToken = "x"; settings.DiscordUsername = "Alice";
        return new WaseBoard.Windows.SettingsWindow(settings, library, "Discord");
    }

    static Window MicPage(WaseBoard.Models.AppSettings settings, WaseBoard.Services.SoundLibraryService library)
    {
        settings.MicFeatureEnabled = true; settings.MicModeHotkey = "Ctrl+Alt+M";
        return new WaseBoard.Windows.SettingsWindow(settings, library, "Mic");
    }

    static Window ClipPage(WaseBoard.Models.AppSettings settings, WaseBoard.Services.SoundLibraryService library)
    {
        settings.ClipEnabled = true; settings.ClipHotkey = "Ctrl+Alt+C"; settings.ClipSeconds = 15;
        return new WaseBoard.Windows.SettingsWindow(settings, library, "Clip");
    }

    static Window ClipTrim()
    {
        // Un clip comme celui que produit le service : 15 s, 48 kHz, stéréo, quelques rafales de bruit.
        int rate = 48000, frames = rate * 15;
        var pcm = new byte[frames * 4];
        var rnd = new Random(3);
        for (int i = 0; i < frames; i++)
        {
            double t = (double)i / rate;
            double env = (t % 3.0 < 0.7 ? 1.0 : 0.05) * (t > 9 ? 1.4 : 0.8);
            short v = (short)((rnd.NextDouble() * 2 - 1) * 12000 * Math.Min(1.5, env));
            BitConverter.GetBytes(v).CopyTo(pcm, i * 4);
            BitConverter.GetBytes(v).CopyTo(pcm, i * 4 + 2);
        }
        var path = Path.Combine(Path.GetTempPath(), "clip-shot.wav");
        WaseBoard.Services.PcmTools.WriteWavFile(path, pcm, rate, 2);
        return new WaseBoard.Windows.TrimWindow(path, "Clip 18:20");
    }

    static Window Diag(WaseBoard.Models.AppSettings settings, WaseBoard.Services.SoundLibraryService library)
    {
        System.Threading.SynchronizationContext.SetSynchronizationContext(new System.Windows.Threading.DispatcherSynchronizationContext(System.Windows.Threading.Dispatcher.CurrentDispatcher));
        library.Settings.ServerUrl = "http://192.168.1.165:5007";
        library.Latency.Add(180, 3); library.Latency.Add(95, 2); library.Latency.Add(310, 1); library.Latency.Add(120, 2);
        return new WaseBoard.Windows.SettingsWindow(settings, library, "Diagnostic");
    }

    static Window SiteEdit()
    {
        var wav = Path.Combine(Path.GetTempPath(), "winshot.wav");
        var extras = new WaseBoard.Windows.EditExtras
        {
            CanEditShared = true,
            Volume = 1.2f, IsFavorite = true, ColorHex = "#38BDF8", FileName = "applaudissements.mp3",
            LocalCategories = new[] { "Mèmes", "Effets" }, SelectedLocalCategories = new[] { "Mèmes" },
            SharedCategories = new[] { new WaseBoard.Windows.SharedCategoryChoice { GuildId = "1", GuildName = "Ballistic Potatoes", IsMember = true, Locked = true } }
        };
        return new WaseBoard.Windows.TrimWindow(wav, "Applaudissements", WaseBoard.Windows.TrimWindowMode.Edit, TimeSpan.FromMilliseconds(250), TimeSpan.FromMilliseconds(3400), "👏", extras);
    }

    static Window EditWindow(bool canEdit)
    {
        var wav = Path.Combine(Path.GetTempPath(), "winshot.wav");
        var extras = new WaseBoard.Windows.EditExtras
        {
            CanEditShared = canEdit,
            Volume = 1.0f, IsFavorite = true, ColorHex = "#F472B6", FileName = "explosion.mp3",
            LocalCategories = new[] { "Effets", "Mèmes" }, SelectedLocalCategories = new[] { "Effets" },
            SharedCategories = new[] { new WaseBoard.Windows.SharedCategoryChoice { GuildId = "1", GuildName = "WaseBoard", IsMember = true, Locked = true } }
        };
        return new WaseBoard.Windows.TrimWindow(wav, "Explosion", WaseBoard.Windows.TrimWindowMode.Edit, null, null, "💥", extras);
    }
}
