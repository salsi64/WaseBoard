using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text.Json;

namespace WaseBoard.Services.Localization
{
    /// <summary>
    /// Textes de l'interface dans la langue de l'utilisateur. Les chaînes vivent dans Localization/fr.json et
    /// Localization/en.json (ressources embarquées, simples fichiers « clé » → « texte » : pas d'assembly satellite,
    /// donc rien de spécial pour la publication en fichier unique).
    ///
    /// Règle : aucun nouveau texte visible en dur dans le XAML ou le C#. En code : <c>Loc.T("update.available", version)</c> ;
    /// en XAML : <c>Content="{loc:Loc settings.title}"</c>. Un texte manquant retombe sur le français, puis sur la clé
    /// elle-même : l'interface n'est jamais cassée par une traduction oubliée (tools/check-localization.ps1, lancé par la
    /// CI, vérifie justement que les deux fichiers ont les mêmes clés).
    ///
    /// La langue se choisit au démarrage (AppSettings.Language : "auto", "fr" ou "en" ; "auto" suit la langue de Windows,
    /// français sinon anglais) ; la changer demande de relancer l'application. Variable d'environnement WASEBOARD_LANG
    /// (fr/en) : prioritaire, pour les essais.
    /// </summary>
    public static class Loc
    {
        public static readonly string[] Supported = { "fr", "en" };

        private const string FallbackLanguage = "fr";

        private static Dictionary<string, string> _current = new();
        private static Dictionary<string, string> _fallback = new();
        private static bool _initialized;

        /// <summary>Langue effectivement utilisée ("fr" ou "en").</summary>
        public static string Language { get; private set; } = FallbackLanguage;

        /// <summary>Culture correspondante (formats de nombres et de dates dans les messages).</summary>
        public static CultureInfo Culture { get; private set; } = CultureInfo.GetCultureInfo(FallbackLanguage);

        /// <summary>Lit la préférence de langue dans settings.json (juste ce champ, sans rien déchiffrer) : appelé au
        /// démarrage, avant la création de la première fenêtre, car les réglages complets ne sont chargés qu'ensuite.</summary>
        public static void InitializeFromSettingsFile()
        {
            string? preference = null;
            try
            {
                var path = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                    AppIdentity.DataFolderName, "settings.json");
                if (File.Exists(path))
                {
                    using var document = JsonDocument.Parse(File.ReadAllText(path));
                    if (document.RootElement.TryGetProperty("Language", out var value) && value.ValueKind == JsonValueKind.String)
                        preference = value.GetString();
                }
            }
            catch
            {
                // Réglages illisibles : on retombe sur la langue de Windows, l'appli se lancera quand même.
            }
            Initialize(preference);
        }

        /// <summary>Choisit la langue (voir <see cref="ResolveLanguage"/>) et charge ses textes.</summary>
        public static void Initialize(string? preference)
        {
            Language = ResolveLanguage(preference, Environment.GetEnvironmentVariable("WASEBOARD_LANG"), CultureInfo.CurrentUICulture);
            Culture = CultureInfo.GetCultureInfo(Language);
            _fallback = Language == FallbackLanguage ? new Dictionary<string, string>() : Load(FallbackLanguage);
            _current = Load(Language);
            _initialized = true;
        }

        /// <summary>"fr" ou "en" : la variable d'environnement d'abord, puis la préférence, puis (« auto ») la langue de Windows.</summary>
        public static string ResolveLanguage(string? preference, string? environmentOverride, CultureInfo systemUiCulture)
        {
            foreach (var candidate in new[] { environmentOverride, preference })
            {
                var code = candidate?.Trim().ToLowerInvariant();
                if (code is not null && Array.IndexOf(Supported, code) >= 0) return code;
            }
            return systemUiCulture.TwoLetterISOLanguageName.Equals("fr", StringComparison.OrdinalIgnoreCase) ? "fr" : "en";
        }

        /// <summary>Le texte pour cette clé (français si absent de la langue courante, la clé elle-même en dernier recours).</summary>
        public static string T(string key)
        {
            if (!_initialized) Initialize(null);
            if (_current.TryGetValue(key, out var text)) return text;
            if (_fallback.TryGetValue(key, out text)) return text;
            return key;
        }

        /// <summary>Idem, avec les paramètres {0}, {1}… remplacés (formats de nombres selon la langue).</summary>
        public static string T(string key, params object[] args)
        {
            var format = T(key);
            try
            {
                return string.Format(Culture, format, args);
            }
            catch (FormatException)
            {
                return format;
            }
        }

        /// <summary>Toutes les clés d'une langue (pour les vérifications de cohérence).</summary>
        public static IReadOnlyCollection<string> Keys(string language) => Load(language).Keys;

        private static Dictionary<string, string> Load(string language)
        {
            var assembly = typeof(Loc).Assembly;
            using var stream = assembly.GetManifestResourceStream($"Localization.{language}.json");
            if (stream is null) return new Dictionary<string, string>();
            return JsonSerializer.Deserialize<Dictionary<string, string>>(stream) ?? new Dictionary<string, string>();
        }
    }
}
