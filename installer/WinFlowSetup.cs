using System;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.IO.Compression;
using System.Media;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
using System.Text;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

[assembly: AssemblyTitle("WinFlow Setup")]
[assembly: AssemblyDescription("WinFlow Standalone Installer")]
[assembly: AssemblyCompany("Piyush Maurya")]
[assembly: AssemblyProduct("WinFlow")]
[assembly: AssemblyCopyright("Copyright (c) 2026 Piyush Maurya")]
[assembly: AssemblyVersion("1.3.1.0")]
[assembly: AssemblyFileVersion("1.3.1.0")]

namespace WinFlowSetup
{
    [ComImport]
    [Guid("00021401-0000-0000-C000-000000000046")]
    internal class ShellLink
    {
    }

    [ComImport]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    [Guid("000214F9-0000-0000-C000-000000000046")]
    internal interface IShellLinkW
    {
        void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszFile, int cch, IntPtr pfd, uint fFlags);
        void GetIDList(out IntPtr ppidl);
        void SetIDList(IntPtr pidl);
        void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszName, int cch);
        void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string pszName);
        void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszDir, int cch);
        void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string pszDir);
        void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszArgs, int cch);
        void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string pszArgs);
        void GetHotkey(out short pwHotkey);
        void SetHotkey(short wHotkey);
        void GetShowCmd(out int piShowCmd);
        void SetShowCmd(int iShowCmd);
        void GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszIconPath, int cch, out int piIcon);
        void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string pszIconPath, int iIcon);
        void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string pszPathRel, uint dwReserved);
        void Resolve(IntPtr hwnd, uint fFlags);
        void SetPath([MarshalAs(UnmanagedType.LPWStr)] string pszFile);
    }

    internal static class NativeMethods
    {
        [DllImport("user32.dll")]
        public static extern bool SetProcessDPIAware();

        [DllImport("dwmapi.dll")]
        public static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int attrValue, int attrSize);
    }

    internal static class ClickAudio
    {
        private static readonly byte[] ClickWav = BuildClickWav();

        private static byte[] BuildClickWav()
        {
            const int sampleRate = 44100;
            const int samples = 750; // ~17ms subtle switch click
            using (var ms = new MemoryStream())
            using (var bw = new BinaryWriter(ms))
            {
                int dataBytes = samples * 2;
                bw.Write(Encoding.ASCII.GetBytes("RIFF"));
                bw.Write(36 + dataBytes);
                bw.Write(Encoding.ASCII.GetBytes("WAVEfmt "));
                bw.Write(16);
                bw.Write((short)1);
                bw.Write((short)1);
                bw.Write(sampleRate);
                bw.Write(sampleRate * 2);
                bw.Write((short)2);
                bw.Write((short)16);
                bw.Write(Encoding.ASCII.GetBytes("data"));
                bw.Write(dataBytes);
                for (int i = 0; i < samples; i++)
                {
                    double t = (double)i / sampleRate;
                    double env = Math.Exp(-t * 260.0);
                    double freq = 1150.0 - 650.0 * ((double)i / samples);
                    double val = Math.Sin(2.0 * Math.PI * freq * t) * env * 0.18;
                    short pcm = (short)Math.Max(-32767, Math.Min(32767, val * 32767));
                    bw.Write(pcm);
                }
                return ms.ToArray();
            }
        }

        public static void Play()
        {
            try
            {
                using (var ms = new MemoryStream(ClickWav))
                using (var sp = new SoundPlayer(ms))
                {
                    sp.Play();
                }
            }
            catch
            {
            }
        }
    }

    internal static class InstallerEngine
    {
        public const string AppVersion = "1.3.1";
        public const string PublisherName = "Piyush Maurya";
        public const string UninstallRegKey = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\WinFlow";

        public static string GetDefaultInstallDir()
        {
            string localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            return Path.Combine(localAppData, "Programs", "WinFlow");
        }

        public static void StopExistingInstances()
        {
            try
            {
                using (EventWaitHandle ev = EventWaitHandle.OpenExisting(@"Local\WinFlowShutdownEvent"))
                {
                    ev.Set();
                    Thread.Sleep(250);
                }
            }
            catch
            {
            }

            try
            {
                int selfId = Process.GetCurrentProcess().Id;
                foreach (var p in Process.GetProcessesByName("WinFlow"))
                {
                    try
                    {
                        if (p.Id != selfId)
                        {
                            p.Kill();
                            p.WaitForExit(2000);
                        }
                    }
                    catch
                    {
                    }
                }
            }
            catch
            {
            }
        }

        public static void CreateShortcut(string lnkPath, string targetExe, string workingDir, string iconPath)
        {
            try
            {
                string parent = Path.GetDirectoryName(lnkPath);
                if (!string.IsNullOrEmpty(parent) && !Directory.Exists(parent))
                {
                    Directory.CreateDirectory(parent);
                }
                var link = (IShellLinkW)new ShellLink();
                link.SetPath(targetExe);
                link.SetWorkingDirectory(workingDir);
                link.SetDescription("WinFlow");
                if (File.Exists(iconPath))
                {
                    link.SetIconLocation(iconPath, 0);
                }
                ((IPersistFile)link).Save(lnkPath, false);
            }
            catch
            {
            }
        }

        public static void InstallPayload(string installDir, bool registerSystemIntegration, Action<double> onProgress)
        {
            onProgress(0.04);
            if (registerSystemIntegration)
            {
                StopExistingInstances();
            }
            onProgress(0.08);

            if (!Directory.Exists(installDir))
            {
                Directory.CreateDirectory(installDir);
            }

            Assembly asm = Assembly.GetExecutingAssembly();
            using (Stream payloadStream = asm.GetManifestResourceStream("WinFlowPayload.zip"))
            {
                if (payloadStream == null)
                {
                    throw new InvalidOperationException("Embedded payload missing.");
                }

                using (var archive = new ZipArchive(payloadStream, ZipArchiveMode.Read))
                {
                    int total = Math.Max(1, archive.Entries.Count);
                    int done = 0;
                    string fullRoot = Path.GetFullPath(installDir);
                    if (!fullRoot.EndsWith(Path.DirectorySeparatorChar.ToString()))
                    {
                        fullRoot += Path.DirectorySeparatorChar;
                    }

                    foreach (ZipArchiveEntry entry in archive.Entries)
                    {
                        string rel = entry.FullName.Replace('/', Path.DirectorySeparatorChar);
                        string destPath = Path.GetFullPath(Path.Combine(installDir, rel));
                        if (!destPath.StartsWith(fullRoot, StringComparison.OrdinalIgnoreCase))
                        {
                            continue;
                        }

                        if (string.IsNullOrEmpty(entry.Name))
                        {
                            Directory.CreateDirectory(destPath);
                        }
                        else
                        {
                            string dir = Path.GetDirectoryName(destPath);
                            if (!string.IsNullOrEmpty(dir) && !Directory.Exists(dir))
                            {
                                Directory.CreateDirectory(dir);
                            }
                            entry.ExtractToFile(destPath, true);
                        }

                        done++;
                        double frac = 0.08 + (0.84 * done / total);
                        onProgress(frac);
                    }
                }
            }

            string exePath = Path.Combine(installDir, "WinFlow.exe");
            string uninstallPath = Path.Combine(installDir, "Uninstall-WinFlow.exe");
            string icoPath = Path.Combine(installDir, "assets", "winflow.ico");
            if (!File.Exists(icoPath))
            {
                string internalIco = Path.Combine(installDir, "_internal", "assets", "winflow.ico");
                if (File.Exists(internalIco))
                {
                    icoPath = internalIco;
                }
                else
                {
                    icoPath = exePath;
                }
            }

            if (registerSystemIntegration)
            {
                string startMenuLnk = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.Programs),
                    "WinFlow.lnk"
                );
                string desktopLnk = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory),
                    "WinFlow.lnk"
                );
                CreateShortcut(startMenuLnk, exePath, installDir, icoPath);
                CreateShortcut(desktopLnk, exePath, installDir, icoPath);

                try
                {
                    using (RegistryKey rk = Registry.CurrentUser.CreateSubKey(UninstallRegKey))
                    {
                        if (rk != null)
                        {
                            rk.SetValue("DisplayName", "WinFlow");
                            rk.SetValue("DisplayVersion", AppVersion);
                            rk.SetValue("Publisher", PublisherName);
                            rk.SetValue("DisplayIcon", icoPath);
                            rk.SetValue("InstallLocation", installDir);
                            rk.SetValue("UninstallString", "\"" + uninstallPath + "\"");
                            rk.SetValue("NoModify", 1, RegistryValueKind.DWord);
                            rk.SetValue("NoRepair", 1, RegistryValueKind.DWord);
                        }
                    }
                }
                catch
                {
                }
            }

            onProgress(1.0);
        }
    }

    internal sealed class ModernButton : Control
    {
        public bool IsPrimary { get; set; }
        private bool _hover;
        private bool _down;

        public ModernButton()
        {
            SetStyle(
                ControlStyles.AllPaintingInWmPaint |
                ControlStyles.OptimizedDoubleBuffer |
                ControlStyles.ResizeRedraw |
                ControlStyles.UserPaint,
                true
            );
            Cursor = Cursors.Hand;
            Font = new Font("Segoe UI", 9.5f, FontStyle.Bold);
        }

        protected override void OnMouseEnter(EventArgs e)
        {
            _hover = true;
            Invalidate();
            base.OnMouseEnter(e);
        }

        protected override void OnMouseLeave(EventArgs e)
        {
            _hover = false;
            _down = false;
            Invalidate();
            base.OnMouseLeave(e);
        }

        protected override void OnMouseDown(MouseEventArgs e)
        {
            if (e.Button == MouseButtons.Left)
            {
                _down = true;
                ClickAudio.Play();
                Invalidate();
            }
            base.OnMouseDown(e);
        }

        protected override void OnMouseUp(MouseEventArgs e)
        {
            _down = false;
            Invalidate();
            base.OnMouseUp(e);
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            Graphics g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            g.Clear(Parent != null ? Parent.BackColor : Color.FromArgb(10, 11, 14));

            Rectangle r = new Rectangle(0, 0, Width - 1, Height - 1);
            if (_down)
            {
                r.Inflate(-1, -1);
            }

            Color fill;
            Color fg;
            Color border;

            if (IsPrimary)
            {
                fill = _hover ? Color.FromArgb(255, 255, 255) : Color.FromArgb(238, 241, 248);
                fg = Color.FromArgb(10, 11, 14);
                border = fill;
            }
            else
            {
                fill = _hover ? Color.FromArgb(26, 29, 38) : Color.FromArgb(18, 20, 26);
                fg = _hover ? Color.FromArgb(243, 245, 248) : Color.FromArgb(155, 162, 182);
                border = Color.FromArgb(40, 44, 56);
            }

            using (GraphicsPath path = RoundedRect(r, 8))
            using (SolidBrush b = new SolidBrush(fill))
            using (Pen p = new Pen(border, 1f))
            {
                g.FillPath(b, path);
                g.DrawPath(p, path);
            }

            TextRenderer.DrawText(
                g,
                Text,
                Font,
                ClientRectangle,
                fg,
                TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter | TextFormatFlags.SingleLine | TextFormatFlags.NoPrefix
            );
        }

        public static GraphicsPath RoundedRect(Rectangle bounds, int radius)
        {
            int d = radius * 2;
            var path = new GraphicsPath();
            path.AddArc(bounds.X, bounds.Y, d, d, 180, 90);
            path.AddArc(bounds.Right - d, bounds.Y, d, d, 270, 90);
            path.AddArc(bounds.Right - d, bounds.Bottom - d, d, d, 0, 90);
            path.AddArc(bounds.X, bounds.Bottom - d, d, d, 90, 90);
            path.CloseFigure();
            return path;
        }
    }

    internal sealed class SetupForm : Form
    {
        private enum ViewState
        {
            License,
            Installing,
            Done,
            Error
        }

        private ViewState _view = ViewState.License;
        private readonly string _installDir;
        private readonly bool _registerSystem;
        private readonly Panel _licenseCard;
        private readonly TextBox _licenseBox;
        private readonly ModernButton _btnCancel;
        private readonly ModernButton _btnInstall;
        private readonly ModernButton _btnOpen;
        private readonly System.Windows.Forms.Timer _animTimer;

        private double _targetProgress;
        private double _displayProgress;
        private volatile bool _installCompleted;
        private volatile string _errorMessage;

        private const string LicenseText =
            "WINFLOW SOFTWARE LICENSE\r\n" +
            "Copyright (c) 2026 Piyush Maurya. All rights reserved.\r\n\r\n" +
            "Permission is hereby granted, free of charge, to any person obtaining a copy of WinFlow " +
            "(\"the Software\"), to install, use, copy, modify, and distribute the Software, subject to " +
            "the following conditions:\r\n\r\n" +
            "1. License & Attribution: The copyright notice above (\"Copyright (c) 2026 Piyush Maurya\") " +
            "and this license notice shall be included in all copies or substantial portions of the Software.\r\n\r\n" +
            "2. Local Privacy: API keys and preferences stay locally on your computer.\r\n\r\n" +
            "3. No Warranty: THE SOFTWARE IS PROVIDED \"AS IS\", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR " +
            "IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY OR FITNESS FOR A PARTICULAR PURPOSE.";

        public SetupForm(string installDir, bool registerSystem, int previewProgressPct)
        {
            _installDir = installDir;
            _registerSystem = registerSystem;

            SetStyle(
                ControlStyles.AllPaintingInWmPaint |
                ControlStyles.OptimizedDoubleBuffer |
                ControlStyles.ResizeRedraw |
                ControlStyles.UserPaint,
                true
            );

            Text = "WinFlow Setup";
            FormBorderStyle = FormBorderStyle.FixedSingle;
            MaximizeBox = false;
            StartPosition = FormStartPosition.CenterScreen;
            BackColor = Color.FromArgb(10, 11, 14);
            ForeColor = Color.FromArgb(243, 245, 248);

            float scale;
            using (Graphics g = CreateGraphics())
            {
                scale = g.DpiX / 96f;
            }
            if (scale < 1f) scale = 1f;

            ClientSize = new Size((int)(460 * scale), (int)(300 * scale));

            try
            {
                Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
            }
            catch
            {
            }

            _licenseCard = new Panel
            {
                BackColor = Color.FromArgb(18, 20, 26),
                Bounds = new Rectangle(
                    (int)(24 * scale),
                    (int)(68 * scale),
                    (int)(412 * scale),
                    (int)(164 * scale)
                )
            };

            _licenseBox = new TextBox
            {
                Multiline = true,
                ReadOnly = true,
                ScrollBars = ScrollBars.Vertical,
                BorderStyle = BorderStyle.None,
                BackColor = Color.FromArgb(18, 20, 26),
                ForeColor = Color.FromArgb(172, 179, 198),
                Font = new Font("Segoe UI", 8.8f, FontStyle.Regular),
                Text = LicenseText,
                Bounds = new Rectangle(
                    (int)(12 * scale),
                    (int)(10 * scale),
                    (int)(388 * scale),
                    (int)(144 * scale)
                ),
                SelectionStart = 0,
                SelectionLength = 0,
                TabStop = false
            };
            _licenseCard.Controls.Add(_licenseBox);
            Controls.Add(_licenseCard);

            _btnCancel = new ModernButton
            {
                Text = "Cancel",
                IsPrimary = false,
                Bounds = new Rectangle(
                    (int)(230 * scale),
                    (int)(246 * scale),
                    (int)(88 * scale),
                    (int)(34 * scale)
                )
            };
            _btnCancel.Click += (s, e) => Close();
            Controls.Add(_btnCancel);

            _btnInstall = new ModernButton
            {
                Text = "Accept & Install",
                IsPrimary = true,
                Bounds = new Rectangle(
                    (int)(326 * scale),
                    (int)(246 * scale),
                    (int)(110 * scale),
                    (int)(34 * scale)
                )
            };
            _btnInstall.Click += (s, e) => BeginInstallation();
            Controls.Add(_btnInstall);

            _btnOpen = new ModernButton
            {
                Text = "Open WinFlow",
                IsPrimary = true,
                Visible = false,
                Bounds = new Rectangle(
                    (int)(165 * scale),
                    (int)(192 * scale),
                    (int)(130 * scale),
                    (int)(36 * scale)
                )
            };
            _btnOpen.Click += (s, e) => LaunchInstalledAppAndClose();
            Controls.Add(_btnOpen);

            _animTimer = new System.Windows.Forms.Timer { Interval = 16 };
            _animTimer.Tick += OnAnimTick;

            if (previewProgressPct >= 0)
            {
                _licenseCard.Visible = false;
                _btnCancel.Visible = false;
                _btnInstall.Visible = false;
                _view = ViewState.Installing;
                _displayProgress = Math.Max(0.05, Math.Min(1.0, previewProgressPct / 100.0));
                _targetProgress = _displayProgress;
            }
        }

        protected override void OnHandleCreated(EventArgs e)
        {
            base.OnHandleCreated(e);
            try
            {
                int dark = 1;
                NativeMethods.DwmSetWindowAttribute(Handle, 20, ref dark, 4);
                int capColor = 0x000E0B0A;
                NativeMethods.DwmSetWindowAttribute(Handle, 35, ref capColor, 4);
            }
            catch
            {
            }
        }

        protected override void OnShown(EventArgs e)
        {
            base.OnShown(e);
            _licenseBox.SelectionLength = 0;
            _btnInstall.Focus();
        }

        private void BeginInstallation()
        {
            _licenseCard.Visible = false;
            _btnCancel.Visible = false;
            _btnInstall.Visible = false;
            _view = ViewState.Installing;
            _targetProgress = 0.05;
            _displayProgress = 0.02;
            _animTimer.Start();
            Invalidate();

            Thread worker = new Thread(() =>
            {
                try
                {
                    InstallerEngine.InstallPayload(
                        _installDir,
                        _registerSystem,
                        p => { _targetProgress = p; }
                    );
                    _installCompleted = true;
                }
                catch (Exception ex)
                {
                    _errorMessage = ex.Message;
                }
            });
            worker.IsBackground = true;
            worker.Start();
        }

        private void OnAnimTick(object sender, EventArgs e)
        {
            if (_errorMessage != null)
            {
                _animTimer.Stop();
                _view = ViewState.Error;
                _btnCancel.Text = "Close";
                _btnCancel.Visible = true;
                Invalidate();
                return;
            }

            double target = _installCompleted ? 1.0 : _targetProgress;
            double diff = target - _displayProgress;
            if (diff > 0.002)
            {
                _displayProgress += Math.Max(0.008, diff * 0.18);
                if (_displayProgress > 1.0) _displayProgress = 1.0;
            }
            else
            {
                _displayProgress = target;
            }

            if (_installCompleted && _displayProgress >= 0.999)
            {
                _displayProgress = 1.0;
                _animTimer.Stop();
                _view = ViewState.Done;
                _btnOpen.Visible = true;
                _btnOpen.Focus();
            }

            Invalidate();
        }

        private void LaunchInstalledAppAndClose()
        {
            try
            {
                string exe = Path.Combine(_installDir, "WinFlow.exe");
                if (File.Exists(exe))
                {
                    Process.Start(new ProcessStartInfo
                    {
                        FileName = exe,
                        WorkingDirectory = _installDir,
                        UseShellExecute = true
                    });
                }
            }
            catch
            {
            }
            Close();
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            base.OnPaint(e);
            Graphics g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            float scale = g.DpiX / 96f;
            if (scale < 1f) scale = 1f;

            // Brand mark + header
            int bx = (int)(24 * scale);
            int by = (int)(22 * scale);
            int bSize = (int)(30 * scale);
            Rectangle badgeRect = new Rectangle(bx, by, bSize, bSize);
            using (GraphicsPath bp = ModernButton.RoundedRect(badgeRect, (int)(8 * scale)))
            using (LinearGradientBrush lb = new LinearGradientBrush(
                badgeRect,
                Color.FromArgb(94, 139, 255),
                Color.FromArgb(59, 104, 242),
                60f))
            {
                g.FillPath(lb, bp);
            }

            // 4 waveform bars inside badge
            int[] barH = { 8, 16, 12, 7 };
            using (SolidBrush wb = new SolidBrush(Color.White))
            {
                for (int i = 0; i < 4; i++)
                {
                    float bw = 2.4f * scale;
                    float bh = barH[i] * scale;
                    float x = bx + (6.5f + i * 5.2f) * scale;
                    float y = by + (bSize - bh) / 2f;
                    g.FillRectangle(wb, x, y, bw, bh);
                }
            }

            using (Font titleFont = new Font("Segoe UI", 12.5f, FontStyle.Bold))
            using (Font subFont = new Font("Segoe UI", 8.5f, FontStyle.Regular))
            {
                TextRenderer.DrawText(
                    g,
                    "WinFlow",
                    titleFont,
                    new Point((int)(64 * scale), (int)(19 * scale)),
                    Color.FromArgb(243, 245, 248)
                );
                TextRenderer.DrawText(
                    g,
                    "Licensed by Piyush Maurya",
                    subFont,
                    new Point((int)(65 * scale), (int)(40 * scale)),
                    Color.FromArgb(118, 126, 148)
                );
            }

            if (_view == ViewState.Installing || _view == ViewState.Done)
            {
                string statusLabel = _view == ViewState.Done ? "Installed" : "Installing…";
                using (Font f = new Font("Segoe UI", 11f, FontStyle.Bold))
                {
                    Rectangle textRect = new Rectangle(
                        0,
                        (int)(116 * scale),
                        ClientSize.Width,
                        (int)(28 * scale)
                    );
                    TextRenderer.DrawText(
                        g,
                        statusLabel,
                        f,
                        textRect,
                        Color.FromArgb(243, 245, 248),
                        TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter
                    );
                }

                // Single clean smooth progress bar — zero file/component details shown
                int barW = (int)(356 * scale);
                int barHeight = (int)(8 * scale);
                int barX = (ClientSize.Width - barW) / 2;
                int barY = (int)(154 * scale);
                Rectangle trackRect = new Rectangle(barX, barY, barW, barHeight);

                using (GraphicsPath tp = ModernButton.RoundedRect(trackRect, barHeight / 2))
                using (SolidBrush tb = new SolidBrush(Color.FromArgb(26, 29, 38)))
                {
                    g.FillPath(tb, tp);
                }

                int fillW = Math.Max(barHeight, (int)Math.Round(barW * _displayProgress));
                if (fillW > barW) fillW = barW;
                Rectangle fillRect = new Rectangle(barX, barY, fillW, barHeight);
                Color barColor = _view == ViewState.Done
                    ? Color.FromArgb(52, 211, 153)
                    : Color.FromArgb(91, 140, 255);

                using (GraphicsPath fp = ModernButton.RoundedRect(fillRect, barHeight / 2))
                using (SolidBrush fb = new SolidBrush(barColor))
                {
                    g.FillPath(fb, fp);
                }
            }
            else if (_view == ViewState.Error)
            {
                using (Font f = new Font("Segoe UI", 9.5f, FontStyle.Regular))
                {
                    Rectangle errRect = new Rectangle(
                        (int)(32 * scale),
                        (int)(110 * scale),
                        ClientSize.Width - (int)(64 * scale),
                        (int)(80 * scale)
                    );
                    TextRenderer.DrawText(
                        g,
                        "Installation error: " + _errorMessage,
                        f,
                        errRect,
                        Color.FromArgb(248, 113, 113),
                        TextFormatFlags.HorizontalCenter | TextFormatFlags.WordBreak
                    );
                }
            }
        }
    }

    internal static class Program
    {
        [STAThread]
        private static int Main(string[] args)
        {
            NativeMethods.SetProcessDPIAware();
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);

            bool silent = false;
            string customDir = null;
            int previewPct = -1;

            foreach (string a in args)
            {
                if (string.Equals(a, "--silent", StringComparison.OrdinalIgnoreCase) ||
                    string.Equals(a, "/S", StringComparison.OrdinalIgnoreCase))
                {
                    silent = true;
                }
                else if (a.StartsWith("--dir=", StringComparison.OrdinalIgnoreCase))
                {
                    customDir = a.Substring("--dir=".Length).Trim('"');
                }
                else if (a.StartsWith("--preview-progress=", StringComparison.OrdinalIgnoreCase))
                {
                    int.TryParse(a.Substring("--preview-progress=".Length), out previewPct);
                }
            }

            string targetDir = string.IsNullOrEmpty(customDir)
                ? InstallerEngine.GetDefaultInstallDir()
                : Path.GetFullPath(customDir);
            bool registerSystem = string.IsNullOrEmpty(customDir);

            if (silent)
            {
                try
                {
                    InstallerEngine.InstallPayload(targetDir, registerSystem, _ => { });
                    return 0;
                }
                catch
                {
                    return 1;
                }
            }

            Application.Run(new SetupForm(targetDir, registerSystem, previewPct));
            return 0;
        }
    }
}
