"""Creates StarCitizenHelperv1.0.py from the supplied stable StarCitizenHelper.py.
Keeps the original untouched and writes a .bak copy of the source for safety.
Run on Windows:  py apply_v1_upgrade.py
"""
from pathlib import Path
import shutil

src=Path('StarCitizenHelper.py')
out=Path('StarCitizenHelperv1.0.py')
if not src.exists():
 raise SystemExit('Put this script beside the supplied StarCitizenHelper.py, then run it again.')
text=src.read_text(encoding='utf-8')
shutil.copy2(src,src.with_suffix('.py.bak'))

def replace(old,new):
 global text
 if old not in text: raise RuntimeError('Expected source text was not found; use the supplied stable source file.')
 text=text.replace(old,new,1)

replace('import json, os, time, threading, queue', 'import json, os, time, threading, queue, ctypes\nfrom ctypes import wintypes')
replace("D={'keepalive_on':'shift+tab+page up','keepalive_off':'shift+tab+page down','keepalive_idle':60,'keepalive_interval':10,'scan_toggle':'ctrl+alt+page up','scan_interval':2,'hold_start':'shift+w+page up','hold_keys':'shift+w','macros':[]}\n", """D={'keepalive_on':'shift+tab+page up','keepalive_off':'shift+tab+page down','keepalive_idle':60,'keepalive_interval':10,'scan_toggle':'ctrl+alt+page up','scan_interval':2,'hold_start':'shift+w+page up','hold_keys':'shift+w','macros':[]}
# Native Windows process checks; no additional package is required.
TH32CS_SNAPPROCESS=2
INVALID_HANDLE_VALUE=ctypes.c_void_p(-1).value
class PROCESSENTRY32W(ctypes.Structure):
 _fields_=[('dwSize',wintypes.DWORD),('cntUsage',wintypes.DWORD),('th32ProcessID',wintypes.DWORD),('th32DefaultHeapID',ctypes.c_size_t),('th32ModuleID',wintypes.DWORD),('cntThreads',wintypes.DWORD),('th32ParentProcessID',wintypes.DWORD),('pcPriClassBase',ctypes.c_long),('dwFlags',wintypes.DWORD),('szExeFile',wintypes.WCHAR*260)]
def process_running(exe):
 try:
  k=ctypes.windll.kernel32;h=k.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS,0)
  if h==INVALID_HANDLE_VALUE:return False
  try:
   p=PROCESSENTRY32W();p.dwSize=ctypes.sizeof(p);ok=k.Process32FirstW(h,ctypes.byref(p))
   while ok:
    if p.szExeFile.casefold()==exe.casefold():return True
    ok=k.Process32NextW(h,ctypes.byref(p))
   return False
  finally:k.CloseHandle(h)
 except:return False
def foreground_is(exe):
 try:
  u=ctypes.windll.user32;k=ctypes.windll.kernel32;hwnd=u.GetForegroundWindow()
  if not hwnd:return False
  pid=wintypes.DWORD();u.GetWindowThreadProcessId(hwnd,ctypes.byref(pid));h=k.OpenProcess(0x1000,False,pid.value)
  if not h:return False
  try:
   b=ctypes.create_unicode_buffer(32768);n=wintypes.DWORD(len(b))
   return bool(k.QueryFullProcessImageNameW(h,0,b,ctypes.byref(n))) and os.path.basename(b.value).casefold()==exe.casefold()
  finally:k.CloseHandle(h)
 except:return False
""")
replace("super().__init__(); s.title('Star Citizen Helper'); s.geometry('980x740')", "super().__init__(); s.title('StarCitizenHelperv1.0'); s.geometry('980x760')")
replace("s.q=queue.Queue();s.stop=threading.Event();s.handles=[];s.keep=s.scan=s.hold=False", "s.q=queue.Queue();s.stop=threading.Event();s.handles=[];s.game_running=False;s.game_foreground=False;s.game_check=0;s.guard_last_log=0;s.keep=s.scan=s.hold=False")
replace("s.ui();s.register()\n  keyboard.on_press(s.activity)", "s.ui();s.register()\n  # Passes all keys through except Alt+F4 when Star Citizen is foreground.\n  s.alt_hook=keyboard.hook(s.alt_f4_guard,suppress=True)\n  keyboard.on_press(s.activity)")
replace("tk.Label(h,text='STAR CITIZEN HELPER',", "tk.Label(h,text='STAR CITIZEN HELPER v1.0',")
replace("s.status=tk.StringVar(value='Waiting for input…');", "s.guard=tk.Label(p,text='Alt+F4 protection: checking for StarCitizen.exe…',bg='#192433',fg='#9ebee0',font=('Segoe UI Semibold',10));s.guard.pack(anchor='w',padx=14,pady=(0,10))\n  s.status=tk.StringVar(value='Waiting for input…');")
replace(" def activity(s,*x):", """ def alt_f4_guard(s,event):
  # keyboard suppresses an event only when this callback returns False.
  if event.event_type==keyboard.KEY_DOWN and event.name=='f4' and keyboard.is_pressed('alt') and foreground_is('StarCitizen.exe'):
   now=time.monotonic()
   if now-s.guard_last_log>1:s.guard_last_log=now;s.q.put('Alt+F4 blocked while Star Citizen is the foreground app.')
   return False
  return True
 def activity(s,*x):""")
replace(" def dashboard(s):\n  def c", """ def dashboard(s):
  now=time.monotonic()
  if now>=s.game_check:
   s.game_running=process_running('StarCitizen.exe');s.game_foreground=s.game_running and foreground_is('StarCitizen.exe');s.game_check=now+1
  if not s.game_running:s.guard.config(text='Alt+F4 protection: INACTIVE — StarCitizen.exe not detected',fg='#9ebee0')
  elif s.game_foreground:s.guard.config(text='Alt+F4 protection: ACTIVE — Star Citizen is foreground; Alt+F4 is blocked',fg='#7de0a9')
  else:s.guard.config(text='Alt+F4 protection: ARMED — StarCitizen.exe detected; activates when it is foreground',fg='#f3cf7a')
  def c""")
replace("  s.stop.set();s.scan=s.keep=False;s.emergency()\n  try:keyboard.unhook_all_hotkeys()", "  s.stop.set();s.scan=s.keep=False;s.emergency()\n  try:keyboard.unhook(s.alt_hook)\n  except:pass\n  try:keyboard.unhook_all_hotkeys()")
out.write_text(text,encoding='utf-8')
print('Created',out.resolve())
print('Original preserved as',src.with_suffix('.py.bak').resolve())