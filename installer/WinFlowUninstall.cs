using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Windows.Forms;
using Microsoft.Win32;

[assembly: AssemblyTitle("WinFlow Uninstaller")]
[assembly: AssemblyDescription("Uninstall WinFlow")]
[assembly: AssemblyCompany("Piyush Maurya")]
[assembly: AssemblyProduct("WinFlow")]
[assembly: AssemblyCopyright("Copyright (c) 2026 Piyush Maurya")]
[assembly: AssemblyVersion("1.4.1.0")]
[assembly: AssemblyFileVersion("1.4.1.0")]

namespace WinFlowUninstall
{
    internal static class Program
    {
        private const string UninstallRegKey = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\WinFlow";
        private const string RunRegKey = @"Software\Microsoft\Windows\CurrentVersion\Run";

        [STAThread]
        private static int Main(string[] args)
        {
            Application.EnableVisualStyles();
            bool silent = false;
            foreach (string a in args)
            {
                if (string.Equals(a, "--silent", StringComparison.OrdinalIgnoreCase) ||
                    string.Equals(a, "/S", StringComparison.OrdinalIgnoreCase))
                {
                    silent = true;
                }
            }

            if (!silent)
            {
                DialogResult ans = MessageBox.Show(
                    "Uninstall WinFlow from your computer?",
                    "WinFlow",
                    MessageBoxButtons.YesNo,
                    MessageBoxIcon.Question
                );
                if (ans != DialogResult.Yes)
                {
                    return 0;
                }
            }

            // Stop running WinFlow instances
            try
            {
                foreach (var p in Process.GetProcessesByName("WinFlow"))
                {
                    try
                    {
                        p.Kill();
                        p.WaitForExit(2000);
                    }
                    catch
                    {
                    }
                }
            }
            catch
            {
            }

            // Remove shortcuts
            try
            {
                string startLnk = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.Programs),
                    "WinFlow.lnk"
                );
                string deskLnk = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory),
                    "WinFlow.lnk"
                );
                if (File.Exists(startLnk)) File.Delete(startLnk);
                if (File.Exists(deskLnk)) File.Delete(deskLnk);
            }
            catch
            {
            }

            // Remove registry entries
            try
            {
                Registry.CurrentUser.DeleteSubKeyTree(UninstallRegKey, false);
            }
            catch
            {
            }

            try
            {
                using (RegistryKey rk = Registry.CurrentUser.OpenSubKey(RunRegKey, true))
                {
                    if (rk != null)
                    {
                        rk.DeleteValue("WinFlow", false);
                    }
                }
            }
            catch
            {
            }

            // Remove installed files except running uninstaller binary
            string appDir = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar);
            string selfExe = Path.GetFullPath(Application.ExecutablePath);
            try
            {
                foreach (string file in Directory.GetFiles(appDir, "*", SearchOption.AllDirectories))
                {
                    if (!string.Equals(Path.GetFullPath(file), selfExe, StringComparison.OrdinalIgnoreCase))
                    {
                        try { File.Delete(file); } catch { }
                    }
                }
                foreach (string dir in Directory.GetDirectories(appDir))
                {
                    try { Directory.Delete(dir, true); } catch { }
                }
            }
            catch
            {
            }

            if (!silent)
            {
                MessageBox.Show("WinFlow has been uninstalled.", "WinFlow", MessageBoxButtons.OK, MessageBoxIcon.Information);
            }
            return 0;
        }
    }
}
