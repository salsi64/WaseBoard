using System;
using System.Security.Cryptography;
using System.Text;

namespace WaseBoard.Services
{
    /// <summary>Chiffrement au repos des secrets stockés dans settings.json (jeton serveur,
    /// session Discord), via DPAPI (lié au profil Windows de l'utilisateur courant). Ni
    /// Protect ni Unprotect ne lèvent jamais : un échec de déchiffrement (profil différent,
    /// blob corrompu) doit se traduire par "secret absent, reconnexion nécessaire", jamais
    /// par un plantage au chargement des réglages.</summary>
    public static class SecretProtector
    {
        private static readonly byte[] Entropy = Encoding.UTF8.GetBytes("WaseBoard.SecretProtector.v1");

        public static string? Protect(string? plaintext)
        {
            if (string.IsNullOrEmpty(plaintext)) return null;
            try
            {
                var bytes = Encoding.UTF8.GetBytes(plaintext);
                var protectedBytes = ProtectedData.Protect(bytes, Entropy, DataProtectionScope.CurrentUser);
                return Convert.ToBase64String(protectedBytes);
            }
            catch
            {
                return null;
            }
        }

        public static string? Unprotect(string? encoded)
        {
            if (string.IsNullOrEmpty(encoded)) return null;
            try
            {
                var protectedBytes = Convert.FromBase64String(encoded);
                var bytes = ProtectedData.Unprotect(protectedBytes, Entropy, DataProtectionScope.CurrentUser);
                return Encoding.UTF8.GetString(bytes);
            }
            catch
            {
                return null;
            }
        }
    }
}
