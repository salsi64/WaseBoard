using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using WaseBoard.Models;
using WaseBoard.Services;
using WaseBoard.Services.Localization;

namespace WaseBoard.Windows
{
    /// <summary>Panel d'administration d'un serveur Discord : sons, corbeille, réglages, statistiques,
    /// journal d'actions, invitation. Réservé aux admins (propriétaire, permission Administrateur ou
    /// rôle désigné) — l'appelant ne l'ouvre que pour les serveurs où l'on est admin, et le serveur
    /// refuse de toute façon (403) toute requête d'un non-admin.</summary>
    public partial class AdminPanelWindow : Window
    {
        private readonly SoundLibraryService _library;
        private readonly List<SoundItem> _knownSounds;

        // Les appels réseau (async void) reprennent sur la fenêtre après un await : si elle a été
        // fermée entre-temps, AlertDialog.Show(this, ...) planterait — même garde que SettingsWindow.
        private bool _isClosed;

        // Chaque chargement incrémente ce compteur : une réponse arrivée après un changement de
        // serveur ou d'onglet est ignorée au lieu d'écraser l'affichage courant.
        private int _loadVersion;

        private string _currentPage = "Sounds";
        private List<SoundRow> _soundRows = new();
        private SoundLibraryService.AdminSettingsInfo? _settingsInfo;

        /// <summary>Vrai si un son a été renommé/supprimé/restauré : l'appelant doit alors recharger le catalogue.</summary>
        public bool CatalogChanged { get; private set; }

        public AdminPanelWindow(SoundLibraryService library, IEnumerable<SoundLibraryService.SharedCategoryInfo> adminGuilds, IEnumerable<SoundItem> knownSounds)
        {
            InitializeComponent();
            _library = library;
            _knownSounds = knownSounds.ToList();
            Closed += (_, _) => _isClosed = true;

            SelectPage("Sounds");
            GuildCombo.ItemsSource = adminGuilds.ToList();
            GuildCombo.SelectedIndex = 0; // déclenche le premier chargement
        }

        private string? CurrentGuildId => (GuildCombo.SelectedItem as SoundLibraryService.SharedCategoryInfo)?.GuildId;

        // ---------- Navigation ----------

        private void NavButton_Click(object sender, RoutedEventArgs e)
        {
            if (sender is Button { Tag: string key })
            {
                SelectPage(key);
                _ = LoadCurrentPageAsync();
            }
        }

        private void SelectPage(string key)
        {
            _currentPage = key;
            var pages = new (string Key, FrameworkElement Page, Button Nav)[]
            {
                ("Sounds", PageSounds, NavSoundsButton),
                ("Trash", PageTrash, NavTrashButton),
                ("Settings", PageSettings, NavSettingsButton),
                ("Stats", PageStats, NavStatsButton),
                ("Audit", PageAudit, NavAuditButton),
                ("Invite", PageInvite, NavInviteButton),
            };

            var accent = (Brush)FindResource("AccentBrush");
            foreach (var (pageKey, page, nav) in pages)
            {
                var isSelected = pageKey == key;
                page.Visibility = isSelected ? Visibility.Visible : Visibility.Collapsed;
                nav.Background = isSelected ? accent : Brushes.Transparent;
                nav.Foreground = (Brush)FindResource(isSelected ? "OnAccentBrush" : "TextBrush");
            }
        }

        private void GuildCombo_SelectionChanged(object sender, SelectionChangedEventArgs e)
        {
            if (_library is null) return; // encore en cours d'InitializeComponent
            _ = LoadCurrentPageAsync();
        }

        private void SetStatus(string text, bool isError = false)
        {
            StatusText.Text = text;
            StatusText.Foreground = isError ? new SolidColorBrush(Color.FromRgb(0xE8, 0x5D, 0x5D)) : (Brush)FindResource("TextBrush");
        }

        private bool IsStale(int version) => _isClosed || version != _loadVersion;

        /// <summary>Recharge les données de l'onglet affiché pour le serveur sélectionné.</summary>
        private async Task LoadCurrentPageAsync()
        {
            var guildId = CurrentGuildId;
            if (guildId is null || _library is null) return;

            var version = ++_loadVersion;
            if (_currentPage == "Invite") { SetStatus(""); return; }

            SetStatus(Loc.T("common.loading"));
            string? error = _currentPage switch
            {
                "Sounds" => await LoadSoundsAsync(guildId, version),
                "Trash" => await LoadTrashAsync(guildId, version),
                "Settings" => await LoadSettingsAsync(guildId, version),
                "Stats" => await LoadStatsAsync(guildId, version),
                "Audit" => await LoadAuditAsync(guildId, version),
                _ => null
            };
            if (IsStale(version)) return;
            if (error is null) SetStatus("");
            else SetStatus(error, isError: true);
        }

        // Chaque Load* renvoie null en cas de succès, sinon le message d'erreur à afficher.
        private string ErrorOrDefault() => string.IsNullOrEmpty(_library.LastErrorDetail) ? Loc.T("admin.load_failed") : _library.LastErrorDetail!;

        // ---------- Sons ----------

        public class SoundRow
        {
            public string Id { get; init; } = "";
            public string Emoji { get; init; } = "";
            public string Name { get; init; } = "";
            public string Author { get; init; } = "";
            public string AuthorTooltip { get; init; } = "";
            public string? AuthorId { get; init; }
            public string Date { get; init; } = "";
            public string SizeText { get; init; } = "";
            public string PlaysText { get; init; } = "";
            public bool CanBlock { get; init; }
            public string BlockTooltip { get; init; } = "";
        }

        private SoundRow ToRow(SoundLibraryService.AdminSound s)
        {
            var unknownAuthor = s.UploadedBy is null;
            var author = unknownAuthor ? Loc.T("admin.old_sound") : s.UploadedByName ?? Loc.T("admin.member", s.UploadedBy ?? "");
            var isMe = s.UploadedBy is not null && s.UploadedBy == _library.Settings.DiscordUserId;
            return new SoundRow
            {
                Id = s.Id,
                Emoji = s.Emoji ?? "",
                Name = s.Name,
                Author = author,
                AuthorTooltip = unknownAuthor ? Loc.T("admin.unknown_author_tooltip") : author,
                AuthorId = s.UploadedBy,
                Date = FormatUnix(s.UploadedAt) ?? "—",
                SizeText = FormatSize(s.Size),
                PlaysText = s.Plays.ToString(),
                CanBlock = !unknownAuthor && !isMe,
                BlockTooltip = unknownAuthor ? Loc.T("admin.unknown_author") : isMe ? Loc.T("admin.its_you") : Loc.T("admin.block_tooltip", author)
            };
        }

        private async Task<string?> LoadSoundsAsync(string guildId, int version)
        {
            var sounds = await _library.GetAdminSoundsAsync(guildId);
            if (IsStale(version)) return null;
            if (sounds is null) return ErrorOrDefault();

            _soundRows = sounds.OrderBy(s => s.Name, StringComparer.CurrentCultureIgnoreCase).Select(ToRow).ToList();
            var withoutAuthor = sounds.Count(s => s.UploadedBy is null);
            SoundsSummaryText.Text = sounds.Count == 0
                ? Loc.T("admin.no_sounds")
                : Loc.T("admin.sounds_count", sounds.Count) + (withoutAuthor > 0 ? Loc.T("admin.sounds_no_author", withoutAuthor) : "");
            ApplySoundFilter();
            return null;
        }

        private void ApplySoundFilter()
        {
            var query = SoundFilterBox.Text.Trim();
            SoundsList.ItemsSource = string.IsNullOrEmpty(query)
                ? _soundRows
                : _soundRows.Where(r => r.Name.Contains(query, StringComparison.CurrentCultureIgnoreCase)
                                        || r.Author.Contains(query, StringComparison.CurrentCultureIgnoreCase)).ToList();
        }

        private void SoundFilterBox_TextChanged(object sender, TextChangedEventArgs e)
        {
            if (_library is null) return;
            ApplySoundFilter();
        }

        private SoundItem? FindKnownSound(string id) => _knownSounds.FirstOrDefault(s => s.Id == id);

        private async void RenameSound_Click(object sender, RoutedEventArgs e)
        {
            if (sender is not Button { Tag: SoundRow row }) return;
            var item = FindKnownSound(row.Id);
            if (item is null)
            {
                AlertDialog.Show(this, Loc.T("admin.sound_not_local"), Loc.T("admin.title"), AlertKind.Warning);
                return;
            }

            var newName = PromptDialog.Show(this, Loc.T("admin.new_name"), row.Name);
            if (string.IsNullOrWhiteSpace(newName) || newName.Trim() == row.Name) return;

            var ok = await _library.RenameSoundAsync(item, newName.Trim());
            if (_isClosed) return;
            if (!ok)
            {
                AlertDialog.Show(this, _library.LastErrorDetail ?? Loc.T("admin.rename_failed"), Loc.T("admin.title"), AlertKind.Warning);
                return;
            }
            CatalogChanged = true;
            await LoadCurrentPageAsync();
        }

        private async void DeleteSound_Click(object sender, RoutedEventArgs e)
        {
            if (sender is not Button { Tag: SoundRow row }) return;
            var item = FindKnownSound(row.Id);
            if (item is null)
            {
                AlertDialog.Show(this, Loc.T("admin.sound_not_local"), Loc.T("admin.title"), AlertKind.Warning);
                return;
            }

            if (!ConfirmDialog.Show(this, Loc.T("admin.delete_confirm", row.Name))) return;

            var ok = await _library.DeleteSoundAsync(item);
            if (_isClosed) return;
            if (!ok)
            {
                AlertDialog.Show(this, _library.LastErrorDetail ?? Loc.T("sounds.delete_failed"), Loc.T("admin.title"), AlertKind.Warning);
                return;
            }
            _knownSounds.Remove(item);
            CatalogChanged = true;
            await LoadCurrentPageAsync();
        }

        private async void BlockAuthor_Click(object sender, RoutedEventArgs e)
        {
            if (sender is not Button { Tag: SoundRow row } || row.AuthorId is null || CurrentGuildId is not { } guildId) return;
            if (!ConfirmDialog.Show(this, Loc.T("admin.block_confirm", row.Author))) return;

            var info = await _library.SetUploaderBlockedAsync(guildId, row.AuthorId, true);
            if (_isClosed) return;
            if (info is null)
            {
                AlertDialog.Show(this, _library.LastErrorDetail ?? Loc.T("admin.block_failed"), Loc.T("admin.title"), AlertKind.Warning);
                return;
            }
            SetStatus(Loc.T("admin.blocked", row.Author));
        }

        // ---------- Corbeille ----------

        public class TrashRow
        {
            public string Id { get; init; } = "";
            public string Emoji { get; init; } = "";
            public string Name { get; init; } = "";
            public string DeletedByText { get; init; } = "";
            public string DeletedAtText { get; init; } = "";
            public string DaysLeftText { get; init; } = "";
        }

        private async Task<string?> LoadTrashAsync(string guildId, int version)
        {
            var trash = await _library.GetAdminTrashAsync(guildId);
            if (IsStale(version)) return null;
            if (trash is null) return ErrorOrDefault();

            TrashList.ItemsSource = trash.Items.Select(t => new TrashRow
            {
                Id = t.Id,
                Emoji = t.Emoji ?? "",
                Name = t.Name,
                DeletedByText = Loc.T("admin.deleted_by", t.DeletedByName ?? (t.DeletedBy is null ? "?" : Loc.T("admin.member", t.DeletedBy))),
                DeletedAtText = FormatUnix((long)t.DeletedAt) ?? "—",
                DaysLeftText = t.DaysLeft is { } d ? Loc.T("admin.days_left", d) : Loc.T("admin.kept_forever")
            }).ToList();

            var retention = trash.RetentionDays > 0
                ? Loc.T("admin.retention", trash.RetentionDays)
                : Loc.T("admin.retention_forever");
            TrashSummaryText.Text = trash.Items.Count == 0 ? Loc.T("admin.trash_empty") + retention : retention;
            return null;
        }

        private async void RestoreSound_Click(object sender, RoutedEventArgs e)
        {
            if (sender is not Button { Tag: TrashRow row } || CurrentGuildId is not { } guildId) return;

            var ok = await _library.RestoreFromTrashAsync(guildId, row.Id);
            if (_isClosed) return;
            if (!ok)
            {
                AlertDialog.Show(this, _library.LastErrorDetail ?? Loc.T("admin.restore_failed"), Loc.T("admin.title"), AlertKind.Warning);
                return;
            }
            CatalogChanged = true;
            await LoadCurrentPageAsync();
            if (!_isClosed) SetStatus(Loc.T("admin.restored", row.Name));
        }

        // ---------- Réglages ----------

        private async Task<string?> LoadSettingsAsync(string guildId, int version)
        {
            var info = await _library.GetAdminSettingsAsync(guildId);
            if (IsStale(version)) return null;
            if (info is null) return ErrorOrDefault();

            ShowSettings(info);
            return null;
        }

        private void ShowSettings(SoundLibraryService.AdminSettingsInfo info)
        {
            _settingsInfo = info;
            var roles = new List<SoundLibraryService.AdminRole> { new() { Id = "", Name = Loc.T("admin.role_none") } };
            roles.AddRange(info.Roles);
            AdminRoleCombo.ItemsSource = roles;
            AdminRoleCombo.SelectedItem = roles.FirstOrDefault(r => r.Id == (info.Settings.AdminRoleId ?? "")) ?? roles[0];

            UploadAdminsOnlyCheck.IsChecked = info.Settings.UploadAdminsOnly;
            MaxSoundsBox.Text = info.Settings.MaxSounds.ToString();
            MaxFileMbBox.Text = info.Settings.MaxFileMb.ToString();
            MaxDurationBox.Text = info.Settings.MaxDurationSeconds.ToString();
            PlayRateBox.Text = info.Settings.PlayRatePerMinute.ToString();
            MaxTotalMbBox.Text = info.Settings.MaxTotalMb.ToString();
            ShowQuotaInfo(info);

            BlockedList.ItemsSource = info.Blocked;
            NoBlockedText.Visibility = info.Blocked.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
        }

        private static readonly Dictionary<string, string> CeilingLabels = new()
        {
            ["max_sounds"] = Loc.T("admin.ceiling.sounds"),
            ["max_file_mb"] = Loc.T("admin.ceiling.file"),
            ["max_duration_s"] = Loc.T("admin.ceiling.duration"),
            ["max_total_mb"] = Loc.T("admin.ceiling.total"),
            ["play_rate_per_min"] = Loc.T("admin.ceiling.antispam")
        };

        /// <summary>Espace consommé et plafonds imposés par l'hébergeur. Un serveur antérieur aux quotas d'espace ne renvoie
        /// ni l'un ni l'autre : le champ « espace disque » est alors masqué (il n'aurait aucun effet).</summary>
        private void ShowQuotaInfo(SoundLibraryService.AdminSettingsInfo info)
        {
            var hasQuotas = info.Usage is not null;
            MaxTotalRow.Visibility = hasQuotas ? Visibility.Visible : Visibility.Collapsed;

            QuotaUsageText.Visibility = hasQuotas ? Visibility.Visible : Visibility.Collapsed;
            if (info.Usage is { } usage)
                QuotaUsageText.Text = Loc.T("admin.quota_usage", usage.Sounds, usage.TotalMb);

            var ceilings = info.Ceilings?
                .Where(c => c.Value > 0 && CeilingLabels.ContainsKey(c.Key))
                .Select(c => $"{CeilingLabels[c.Key]} ≤ {c.Value}")
                .ToList();
            var hasCeilings = ceilings is { Count: > 0 };
            CeilingsText.Visibility = hasCeilings ? Visibility.Visible : Visibility.Collapsed;
            if (hasCeilings)
                CeilingsText.Text = Loc.T("admin.ceilings", string.Join(" ; ", ceilings!));
        }

        private static bool TryParseCount(string text, out int value) =>
            int.TryParse(text.Trim(), out value) && value >= 0;

        private async void SaveSettings_Click(object sender, RoutedEventArgs e)
        {
            if (CurrentGuildId is not { } guildId) return;

            if (!TryParseCount(MaxSoundsBox.Text, out var maxSounds) || !TryParseCount(MaxFileMbBox.Text, out var maxFileMb)
                || !TryParseCount(MaxDurationBox.Text, out var maxDuration) || !TryParseCount(PlayRateBox.Text, out var playRate)
                || !TryParseCount(MaxTotalMbBox.Text, out var maxTotalMb))
            {
                AlertDialog.Show(this, Loc.T("admin.limits_invalid"), Loc.T("admin.title"), AlertKind.Warning);
                return;
            }

            var selectedRole = AdminRoleCombo.SelectedItem as SoundLibraryService.AdminRole;
            var settings = new SoundLibraryService.AdminSettings
            {
                AdminRoleId = string.IsNullOrEmpty(selectedRole?.Id) ? null : selectedRole!.Id,
                UploadAdminsOnly = UploadAdminsOnlyCheck.IsChecked == true,
                MaxSounds = maxSounds,
                MaxFileMb = maxFileMb,
                MaxDurationSeconds = maxDuration,
                MaxTotalMb = maxTotalMb,
                PlayRatePerMinute = playRate
            };

            var info = await _library.UpdateAdminSettingsAsync(guildId, settings);
            if (_isClosed) return;
            if (info is null)
            {
                AlertDialog.Show(this, _library.LastErrorDetail ?? Loc.T("admin.save_failed"), Loc.T("admin.title"), AlertKind.Warning);
                return;
            }
            ShowSettings(info);
            SetStatus(Loc.T("admin.saved"));
        }

        private async void Unblock_Click(object sender, RoutedEventArgs e)
        {
            if (sender is not Button { Tag: SoundLibraryService.AdminBlockedUser user } || CurrentGuildId is not { } guildId) return;

            var info = await _library.SetUploaderBlockedAsync(guildId, user.UserId, false);
            if (_isClosed) return;
            if (info is null)
            {
                AlertDialog.Show(this, _library.LastErrorDetail ?? Loc.T("admin.unblock_failed"), Loc.T("admin.title"), AlertKind.Warning);
                return;
            }
            ShowSettings(info);
            SetStatus(Loc.T("admin.unblocked", user.DisplayName));
        }

        // ---------- Statistiques ----------

        public class DayBar
        {
            public double BarHeight { get; init; }
            public string Tooltip { get; init; } = "";
        }

        public class RankRow
        {
            public string Emoji { get; init; } = "";
            public string Name { get; init; } = "";
            public string? AvatarUrl { get; init; }
            public int Count { get; init; }
            public double BarWidth { get; init; }
        }

        private void StatsPeriodCombo_SelectionChanged(object sender, SelectionChangedEventArgs e)
        {
            if (_library is null || _currentPage != "Stats") return;
            _ = LoadCurrentPageAsync();
        }

        private async Task<string?> LoadStatsAsync(string guildId, int version)
        {
            var days = StatsPeriodCombo.SelectedItem is ComboBoxItem { Tag: string tag } && int.TryParse(tag, out var d) ? d : 30;
            var stats = await _library.GetAdminStatsAsync(guildId, days);
            if (IsStale(version)) return null;
            if (stats is null) return ErrorOrDefault();

            StatsTotalText.Text = Loc.T("admin.stats_total", stats.TotalPlays, stats.Days);

            const double MaxBarHeight = 104;
            var maxDay = Math.Max(1, stats.PerDay.Select(p => p.Count).DefaultIfEmpty(0).Max());
            DayBars.ItemsSource = stats.PerDay.Select(p => new DayBar
            {
                BarHeight = p.Count == 0 ? 2 : 4 + (MaxBarHeight - 4) * p.Count / maxDay,
                Tooltip = Loc.T("admin.stats_day_tooltip", FormatIsoDay(p.Date), p.Count)
            }).ToList();
            DayAxisStartText.Text = stats.PerDay.Count > 0 ? FormatIsoDay(stats.PerDay[0].Date) : "";
            DayAxisEndText.Text = stats.PerDay.Count > 0 ? FormatIsoDay(stats.PerDay[^1].Date) : "";

            const double MaxRankBar = 200;
            var maxSound = Math.Max(1, stats.TopSounds.Select(s => s.Count).DefaultIfEmpty(0).Max());
            TopSoundsList.ItemsSource = stats.TopSounds.Select(s => new RankRow
            {
                Emoji = s.Emoji ?? "", Name = s.Name, Count = s.Count, BarWidth = Math.Max(4, MaxRankBar * s.Count / maxSound)
            }).ToList();
            NoTopSoundsText.Visibility = stats.TopSounds.Count == 0 ? Visibility.Visible : Visibility.Collapsed;

            var maxUser = Math.Max(1, stats.TopUsers.Select(u => u.Count).DefaultIfEmpty(0).Max());
            TopUsersList.ItemsSource = stats.TopUsers.Select(u => new RankRow
            {
                Name = u.Username ?? Loc.T("admin.member", u.UserId), AvatarUrl = u.AvatarUrl, Count = u.Count,
                BarWidth = Math.Max(4, MaxRankBar * u.Count / maxUser)
            }).ToList();
            return null;
        }

        // ---------- Journal ----------

        public class AuditRow
        {
            public string When { get; init; } = "";
            public string Description { get; init; } = "";
        }

        private async Task<string?> LoadAuditAsync(string guildId, int version)
        {
            var entries = await _library.GetAdminAuditAsync(guildId);
            if (IsStale(version)) return null;
            if (entries is null) return ErrorOrDefault();

            AuditList.ItemsSource = entries.Select(en => new AuditRow
            {
                When = FormatUnix((long)en.Timestamp, withTime: true) ?? "",
                Description = DescribeAudit(en)
            }).ToList();
            AuditSummaryText.Text = entries.Count == 0
                ? Loc.T("admin.audit_none")
                : Loc.T("admin.audit_header", entries.Count);
            return null;
        }

        private static string? Detail(SoundLibraryService.AdminAuditEntry entry, string key)
        {
            if (entry.Details is null || !entry.Details.TryGetValue(key, out var value)) return null;
            return value.ValueKind switch
            {
                JsonValueKind.String => value.GetString(),
                JsonValueKind.Null => null,
                _ => value.ToString()
            };
        }

        private static string DescribeAudit(SoundLibraryService.AdminAuditEntry en)
        {
            var actor = en.ActorName ?? Loc.T("admin.audit.someone");
            var sound = en.SoundName is null ? Loc.T("admin.audit.a_sound") : Loc.T("admin.audit.sound_name", en.SoundName);
            return en.Action switch
            {
                "upload" => Loc.T("admin.audit.upload", actor, sound),
                "rename" => Loc.T("admin.audit.rename", actor, Detail(en, "from") ?? "", Detail(en, "to") ?? ""),
                "emoji" => Loc.T("admin.audit.emoji", actor, sound, Detail(en, "emoji") ?? ""),
                "trim" => Loc.T("admin.audit.trim", actor, sound),
                "replace" => Loc.T("admin.audit.replace", actor, sound),
                "delete" => Loc.T("admin.audit.delete", actor, sound),
                "restore" => Loc.T("admin.audit.restore", actor, sound),
                "share_add" => Loc.T("admin.audit.share_add", actor, sound),
                "share_remove" => Loc.T("admin.audit.share_remove", actor, sound),
                "block" => Loc.T("admin.audit.block", actor, Detail(en, "username") ?? Loc.T("admin.audit.the_member", Detail(en, "user_id") ?? "")),
                "unblock" => Loc.T("admin.audit.unblock", actor, Detail(en, "username") ?? Loc.T("admin.audit.the_member", Detail(en, "user_id") ?? "")),
                "purge" => Loc.T("admin.audit.purge", sound),
                "settings" => Loc.T("admin.audit.settings", actor, DescribeSettingsChange(en)),
                _ => Loc.T("admin.audit.other", actor, en.Action)
            };
        }

        private static readonly Dictionary<string, string> SettingLabels = new()
        {
            ["admin_role_id"] = Loc.T("admin.setting.admin_role"),
            ["upload_admins_only"] = Loc.T("admin.setting.upload_admins_only"),
            ["max_sounds"] = Loc.T("admin.setting.max_sounds"),
            ["max_file_mb"] = Loc.T("admin.setting.max_file"),
            ["max_duration_s"] = Loc.T("admin.setting.max_duration"),
            ["max_total_mb"] = Loc.T("admin.setting.max_total"),
            ["play_rate_per_min"] = Loc.T("admin.ceiling.antispam")
        };

        private static string DescribeSettingsChange(SoundLibraryService.AdminAuditEntry en)
        {
            if (en.Details is null || en.Details.Count == 0) return Loc.T("admin.audit.no_detail");

            static string Show(JsonElement v) => v.ValueKind switch
            {
                JsonValueKind.Null => Loc.T("common.none_lower"),
                JsonValueKind.True => Loc.T("common.yes_lower"),
                JsonValueKind.False => Loc.T("common.no_lower"),
                JsonValueKind.String => v.GetString() ?? Loc.T("common.none_lower"),
                _ => v.ToString()
            };

            return string.Join(", ", en.Details.Select(kv =>
            {
                var label = SettingLabels.TryGetValue(kv.Key, out var l) ? l : kv.Key;
                return kv.Value.ValueKind == JsonValueKind.Array && kv.Value.GetArrayLength() == 2
                    ? $"{label} ({Show(kv.Value[0])} → {Show(kv.Value[1])})"
                    : label;
            }));
        }

        // ---------- Invitation ----------

        private void CopyInvite_Click(object sender, RoutedEventArgs e)
        {
            var url = _library.Settings.ServerUrl.Trim();
            var token = _library.Settings.ServerToken;
            if (string.IsNullOrEmpty(url) || string.IsNullOrEmpty(token))
            {
                AlertDialog.Show(this, Loc.T("admin.fill_first"), Loc.T("admin.title"), AlertKind.Warning);
                return;
            }

            Clipboard.SetText($"waseboard://connect?url={Uri.EscapeDataString(url)}&token={Uri.EscapeDataString(token)}");

            var original = CopyInviteButton.Content;
            CopyInviteButton.Content = Loc.T("settings.diagnostic.copied");
            var timer = new System.Windows.Threading.DispatcherTimer { Interval = TimeSpan.FromSeconds(2) };
            timer.Tick += (_, _) => { CopyInviteButton.Content = original; timer.Stop(); };
            timer.Start();
        }

        // ---------- Formats ----------

        private static string? FormatUnix(long? seconds, bool withTime = false)
        {
            if (seconds is null) return null;
            var local = DateTimeOffset.FromUnixTimeSeconds(seconds.Value).ToLocalTime();
            return local.ToString(withTime ? Loc.T("format.date_time") : Loc.T("format.date"));
        }

        private static string FormatIsoDay(string isoDate) =>
            DateTime.TryParse(isoDate, out var d) ? d.ToString(Loc.T("format.day_month")) : isoDate;

        private static string FormatSize(long? bytes) => bytes switch
        {
            null => "—",
            < 1024 * 1024 => Loc.T("format.size_kb", Math.Max(1, bytes.Value / 1024)),
            _ => Loc.T("format.size_mb", bytes.Value / (1024.0 * 1024.0))
        };
    }
}
