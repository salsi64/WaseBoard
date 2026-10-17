using System;
using System.Collections;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.IO;
using System.Reflection;
using System.Threading;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using WaseBoard.Models;

static class MainShot
{
    const BindingFlags NP = BindingFlags.NonPublic | BindingFlags.Instance;

    static string FileTheme(object lib)
    {
        var f = lib.GetType().GetField("_settingsFilePath", BindingFlags.NonPublic | BindingFlags.Instance);
        var path = (string?)f?.GetValue(lib);
        if (path == null) return "?path";
        var txt = File.Exists(path) ? File.ReadAllText(path) : "";
        var i = txt.IndexOf("UiTheme"); var j = txt.IndexOf("FollowSystemTheme");
        return path + " :: " + (i >= 0 ? txt.Substring(i, Math.Min(22, txt.Length - i)) : "no-UiTheme") + " / " + (j >= 0 ? txt.Substring(j, Math.Min(28, txt.Length - j)) : "");
    }
    static void Dump(string label) => Console.WriteLine(label + ": Field=" + ((System.Windows.Media.SolidColorBrush)Application.Current.Resources["FieldBrush"]).Color + " theme=" + (Application.Current.MainWindow == null ? "-" : "w"));
    static void Pump(int ms)
    {
        var end = DateTime.Now.AddMilliseconds(ms);
        while (DateTime.Now < end) { Dispatcher.CurrentDispatcher.Invoke(() => { }, DispatcherPriority.Background); Thread.Sleep(20); }
    }

    static SoundItem Snd(string name, string emoji, string? color = null, bool playing = false, bool fav = false)
    {
        var rnd = new Random(name.GetHashCode());
        var peaks = new float[28];
        for (int i = 0; i < peaks.Length; i++) peaks[i] = 0.15f + (float)rnd.NextDouble() * 0.85f;
        return new SoundItem { Name = name, Emoji = emoji, ColorHex = color, IsPlaying = playing, IsFavorite = fav, WaveformPeaks = peaks, DurationMs = 3800, CanEdit = true };
    }

    public static void Run(string outPng, string theme, string palette, int width, int height)
    {
        var app = new WaseBoard.App();
        app.InitializeComponent();
        typeof(Application).GetField("_startupUri", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance)?.SetValue(app, null); // évite la 2e MainWindow (StartupUri) ouverte par le Pump

        app.ShutdownMode = ShutdownMode.OnExplicitShutdown;

        var w = new WaseBoard.MainWindow();
        var libField = typeof(WaseBoard.MainWindow).GetField("_library", NP)!;
        var lib = libField.GetValue(w)!;
        var settings = lib.GetType().GetProperty("Settings")!.GetValue(lib)!;
        settings.GetType().GetProperty("UiTheme")!.SetValue(settings, theme);
        settings.GetType().GetProperty("PaletteId")!.SetValue(settings, palette);
        settings.GetType().GetProperty("FollowSystemTheme")!.SetValue(settings, false);
        settings.GetType().GetProperty("FollowSystemAccent")!.SetValue(settings, false);
        settings.GetType().GetProperty("ShowWaveforms")!.SetValue(settings, true);

        var t = typeof(WaseBoard.MainWindow);
        t.GetMethod("ApplyColorScheme", NP)!.Invoke(w, null);
        t.GetMethod("ApplyTheme", NP)!.Invoke(w, null);
        Console.WriteLine("UiTheme=" + settings.GetType().GetProperty("UiTheme")!.GetValue(settings) + " Field=" + ((System.Windows.Media.SolidColorBrush)Application.Current.Resources["FieldBrush"]).Color + " Primary=" + Application.Current.Resources["PrimaryBrush"].GetType().Name);

        // Sections factices
        var secType = t.GetNestedType("SectionViewModel", BindingFlags.NonPublic)!;
        object Section(string name, string key, bool manageable, bool shared, bool expanded, bool allSounds, params SoundItem[] sounds)
        {
            var s = Activator.CreateInstance(secType)!;
            secType.GetProperty("Name")!.SetValue(s, name);
            secType.GetProperty("CategoryKey")!.SetValue(s, key);
            secType.GetProperty("IsManageable")!.SetValue(s, manageable);
            secType.GetProperty("IsShared")!.SetValue(s, shared);
            secType.GetProperty("IsExpanded")!.SetValue(s, expanded);
            secType.GetProperty("IsReorderable")!.SetValue(s, manageable || shared);
            secType.GetProperty("IsAllSounds")!.SetValue(s, allSounds);
            secType.GetProperty("Sounds")!.SetValue(s, new ObservableCollection<SoundItem>(sounds));
            return s;
        }
        var list = (IList)Activator.CreateInstance(typeof(List<>).MakeGenericType(secType))!;
        list.Add(Section("★ Favoris", "__fav__", false, false, true, false,
            Snd("Applaudissements", "👏", "#38BDF8", false, true), Snd("Victoire", "🏆", "#F472B6", true, true), Snd("Rire", "😂", "#34D399", false, true)));
        list.Add(Section("Mèmes", "Mèmes", true, false, true, false,
            Snd("Bruh", "💀"), Snd("Vine boom", "💥", "#FBBF24"), Snd("Oh no", "😱"), Snd("Sad violin", "🎻", "#A78BFA")));
        list.Add(Section("Effets", "Effets", true, false, false, false, Snd("Explosion", "💥"), Snd("Alerte", "🚨")));
        list.Add(Section("WaseBoard", "1001", false, true, true, true,
            Snd("Fanfare", "🎺"), Snd("Tambour", "🥁", "#F87171"), Snd("Bravo", "🎉"), Snd("Cloche", "🔔"), Snd("Applaudissements très longs", "👏"), Snd("Alerte", "🚨")));

        Dump("A-before-sections"); Console.WriteLine("FILE@A " + FileTheme(lib));
        // FORCE-LABELS : la fenêtre n'est pas affichée (ActualWidth = 0) donc la barre se croit « étroite » ; on force les libellés.
        foreach (var n in new[] { "JoinVoiceLabel", "StopAllLabel", "AddCategoryLabel" }) ((FrameworkElement)w.FindName(n)).Visibility = Visibility.Visible;
        ((System.Windows.Controls.TextBlock)w.FindName("AddSoundLabel")).Text = "Ajouter un son";
        var items = (System.Windows.Controls.ItemsControl)w.FindName("SectionsItemsControl");
        items.ItemsSource = list;

        var root = (FrameworkElement)w.Content;
        root.Measure(new Size(width, height));
        root.Arrange(new Rect(0, 0, width, height));
        root.UpdateLayout();
        Dump("B-after-layout");
        Pump(900);
        Dump("C-after-pump"); Console.WriteLine("FILE@C " + FileTheme(lib));
        // Les sections ne s'ouvrent que via Loaded (fenêtre non affichée ici) : on ouvre à la main celles qui doivent l'être.
        void Open(DependencyObject d)
        {
            for (int i = 0; i < VisualTreeHelper.GetChildrenCount(d); i++)
            {
                var c = VisualTreeHelper.GetChild(d, i);
                if (c is FrameworkElement fe && fe.Name == "ExpandSiteBorder" && fe.TemplatedParent is System.Windows.Controls.Expander ex && ex.IsExpanded)
                    fe.MaxHeight = double.PositiveInfinity;
                Open(c);
            }
        }
        // Window_Loaded (déclenché par le Pump) recharge les réglages depuis le disque : on ré-applique le thème voulu.
        {
            var lib2 = libField.GetValue(w)!;
            var st2 = lib2.GetType().GetProperty("Settings")!.GetValue(lib2)!;
            st2.GetType().GetProperty("UiTheme")!.SetValue(st2, theme);
            st2.GetType().GetProperty("PaletteId")!.SetValue(st2, palette);
            st2.GetType().GetProperty("FollowSystemTheme")!.SetValue(st2, false);
            st2.GetType().GetProperty("FollowSystemAccent")!.SetValue(st2, false);
            st2.GetType().GetProperty("ShowWaveforms")!.SetValue(st2, true);
            t.GetMethod("ApplyColorScheme", NP)!.Invoke(w, null);
            t.GetMethod("ApplyTheme", NP)!.Invoke(w, null);
            items.ItemsSource = list;
            root.Measure(new Size(width, height)); root.Arrange(new Rect(0, 0, width, height)); root.UpdateLayout();
            Pump(500);
        }
        if (Environment.GetEnvironmentVariable("SHOT_CLIP") == "1")
        {
            var lib3 = libField.GetValue(w)!;
            var st3 = (WaseBoard.Models.AppSettings)lib3.GetType().GetProperty("Settings")!.GetValue(lib3)!;
            st3.ClipEnabled = true; st3.ClipSeconds = 15; st3.ClipHotkey = "Ctrl+Alt+C";
            t.GetMethod("ApplyClipSettings", NP)!.Invoke(w, null);
            Pump(300);
            var btn = (FrameworkElement)w.FindName("ClipButton");
            var dot = (FrameworkElement)w.FindName("ClipRecDot");
            Console.WriteLine("clip : bouton=" + btn.Visibility + " voyant=" + dot.Visibility + " | " + ((System.Windows.Controls.Button)btn).ToolTip);
        }
        Open(root);
        root.Measure(new Size(width, height));
        root.Arrange(new Rect(0, 0, width, height));
        root.UpdateLayout();
        Pump(400);

        foreach (var n in new[] { "JoinVoiceLabel", "StopAllLabel", "AddCategoryLabel" }) ((FrameworkElement)w.FindName(n)).Visibility = Visibility.Visible;
        ((System.Windows.Controls.TextBlock)w.FindName("AddSoundLabel")).Text = "Ajouter un son";
        root.Measure(new Size(width, height)); root.Arrange(new Rect(0, 0, width, height)); root.UpdateLayout();
        Pump(300);
        Console.WriteLine("render-time: Field=" + ((System.Windows.Media.SolidColorBrush)Application.Current.Resources["FieldBrush"]).Color + " Primary=" + Application.Current.Resources["PrimaryBrush"].GetType().Name + " searchBg=" + ((System.Windows.Media.SolidColorBrush)((System.Windows.Controls.TextBox)w.FindName("SearchBox")).Background).Color);
        var scale = 1.5;
        var rtb = new RenderTargetBitmap((int)(width * scale), (int)(height * scale), 96 * scale, 96 * scale, PixelFormats.Pbgra32);
        rtb.Render(root);
        var enc = new PngBitmapEncoder();
        enc.Frames.Add(BitmapFrame.Create(rtb));
        using (var fs = File.Create(outPng)) enc.Save(fs);
        Console.WriteLine($"MAIN OK {theme}/{palette} -> {outPng}");
        app.Shutdown();
    }
}
