import json, os, time, threading, queue
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import keyboard
try:
 from pynput import mouse
except ImportError: mouse=None
P=os.path.dirname(os.path.abspath(__file__)); F=os.path.join(P,'settings.json')
D={'keepalive_on':'shift+tab+page up','keepalive_off':'shift+tab+page down','keepalive_idle':60,'keepalive_interval':10,'scan_toggle':'ctrl+alt+page up','scan_interval':2,'hold_start':'shift+w+page up','hold_keys':'shift+w','macros':[]}
class App(tk.Tk):
 def __init__(s):
  super().__init__(); s.title('Star Citizen Helper'); s.geometry('980x740'); s.minsize(860,630); s.configure(bg='#101722'); s.protocol('WM_DELETE_WINDOW',s.close)
  s.cfg=D.copy();
  s.running_macro=''
  try:
   with open(F,encoding='utf8') as f:s.cfg.update(json.load(f))
  except:pass
  if not isinstance(s.cfg.get('macros'),list):s.cfg['macros']=[]
  s.settings_source='Local settings.json' if os.path.exists(F) else 'Built-in defaults (not saved yet)'
  s.q=queue.Queue();s.stop=threading.Event();s.handles=[];s.keep=s.scan=s.hold=False;s.held=[];s.hold_pending=False;s.hold_token=0;s.last=time.monotonic();s.nextk=s.nexts=0;s.ignore=0;s.ui();s.register()
  keyboard.on_press(s.activity) # keyboard module does not have on_click
  s.ml=None
  if mouse:
   s.ml=mouse.Listener(on_move=lambda x,y:s.activity(),on_click=lambda x,y,b,p:s.activity(),on_scroll=lambda x,y,dx,dy:s.activity());s.ml.daemon=True;s.ml.start()
  threading.Thread(target=s.loop,daemon=True).start();s.after(100,s.events);s.after(200,s.dashboard);s.log('Ready. Global hotkeys registered.')
 def ui(s):
  st=ttk.Style(s);st.theme_use('clam');st.configure('TNotebook',background='#101722');st.configure('TNotebook.Tab',background='#1c2938',foreground='#c9d7e6',padding=(16,9));st.map('TNotebook.Tab',background=[('selected','#2a4661')])
  h=tk.Frame(s,bg='#101722');h.pack(fill='x',padx=22,pady=(18,5));tk.Label(h,text='STAR CITIZEN HELPER',bg='#101722',fg='#eef6ff',font=('Segoe UI Semibold',18)).pack(anchor='w');tk.Label(h,text='Automation status and hotkey controls',bg='#101722',fg='#91a7bd').pack(anchor='w')
  p=tk.Frame(s,bg='#192433',highlightbackground='#2e435a',highlightthickness=1);p.pack(fill='x',padx=22,pady=10);tk.Label(p,text='ACTIVE AUTOMATIONS',bg='#192433',fg='#91a7bd',font=('Segoe UI Semibold',9)).pack(anchor='w',padx=14,pady=(10,4));r=tk.Frame(p,bg='#192433');r.pack(fill='x',padx=12,pady=(0,12));s.chips={}
  for x in ('Keepalive','Ship Scan','KeepRunning','Macro'):
   z=tk.Label(r,text=x+': OFF',bg='#253448',fg='#b6c5d5',font=('Segoe UI Semibold',10),padx=12,pady=6);z.pack(side='left',padx=(0,8));s.chips[x]=z
  s.status=tk.StringVar(value='Waiting for input…');tk.Label(s,textvariable=s.status,bg='#101722',fg='#b5c9dc').pack(anchor='w',padx=24,pady=(0,5))
  # These controls deliberately live above the tabs so they remain visible on every page,
  # including the taller Macros and Activity Log pages.
  controls=tk.Frame(s,bg='#192433',highlightbackground='#2e435a',highlightthickness=1);controls.pack(fill='x',padx=22,pady=(0,10))
  cr=tk.Frame(controls,bg='#192433');cr.pack(fill='x',padx=12,pady=(10,4))
  tk.Button(cr,text='Save Settings',command=s.save,bg='#2a6f9e',fg='white',relief='flat',padx=14,pady=7).pack(side='left')
  tk.Button(cr,text='Backup Settings (.json)',command=s.backup_hotkeys,bg='#466f91',fg='white',relief='flat',padx=12,pady=7).pack(side='left',padx=(8,0))
  tk.Button(cr,text='Import Settings (.json)',command=s.import_hotkeys,bg='#466f91',fg='white',relief='flat',padx=12,pady=7).pack(side='left',padx=8)
  tk.Button(cr,text='Stop & Release',command=s.release,bg='#a65a46',fg='white',relief='flat',padx=12,pady=7).pack(side='left')
  tk.Button(cr,text='EMERGENCY DISABLE ALL',command=s.emergency,bg='#8b3f48',fg='white',relief='flat',padx=12,pady=7).pack(side='left',padx=8)
  s.json_status=tk.StringVar();tk.Label(controls,textvariable=s.json_status,bg='#192433',fg='#9ebee0',anchor='w').pack(fill='x',padx=14,pady=(2,10))
  s.update_json_indicator()
  s.vars={k:tk.StringVar(value=str(v)) for k,v in s.cfg.items() if k!='macros'};n=ttk.Notebook(s);n.pack(fill='both',expand=True,padx=22,pady=(0,10));s.tab(n,'Keepalive','Inactivity keepalive','After no physical mouse/keyboard activity, sends Tab at the chosen interval.', [('Enable hotkey','keepalive_on','Shift+Tab+Page Up'),('Disable hotkey','keepalive_off','Shift+Tab+Page Down'),('Idle seconds','keepalive_idle','60'),('Tab interval seconds','keepalive_interval','10')]);s.tab(n,'Scan Ships','Ship Scan','Independent of inactivity: sends Tab continuously even while you use your keyboard or mouse.', [('Toggle hotkey','scan_toggle','Ctrl+Alt+Page Up'),('Tab interval seconds','scan_interval','2')],s.toggle_scan);s.tab(n,'KeepRunning','Toggle held keys','Press the same toggle hotkey to start or stop holding the selected keys.', [('Toggle hotkey','hold_start','Shift+W+Page Up'),('Keys to hold','hold_keys','shift+w')]);s.macro_tab(n);t=tk.Frame(n,bg='#192433');n.add(t,text='Activity Log');s.out=tk.Text(t,bg='#0f1721',fg='#cce0f0',relief='flat',state='disabled',font=('Consolas',10));s.out.pack(fill='both',expand=True,padx=14,pady=14)
 def tab(s,n,name,title,desc,fields,button=None):
  t=tk.Frame(n,bg='#192433');n.add(t,text=name);tk.Label(t,text=title,bg='#192433',fg='#eef6ff',font=('Segoe UI Semibold',14)).pack(anchor='w',padx=20,pady=(18,4));tk.Label(t,text=desc,bg='#192433',fg='#9eb2c6',wraplength=780,justify='left').pack(anchor='w',padx=20,pady=(0,12))
  for label,key,hint in fields:
   r=tk.Frame(t,bg='#192433');r.pack(fill='x',padx=20,pady=8);tk.Label(r,text=label,bg='#192433',fg='#eef6ff',width=25,anchor='w').pack(side='left');tk.Entry(r,textvariable=s.vars[key],bg='#0f1721',fg='#eaf4ff',insertbackground='white',relief='flat',width=28).pack(side='left',padx=8,ipady=5);tk.Label(r,text='default: '+hint,bg='#192433',fg='#8ca2b9').pack(side='left')
  if button:tk.Button(t,text='Toggle Ship Scan',command=button,bg='#2a6f9e',fg='white',relief='flat',padx=16,pady=8).pack(anchor='w',padx=20,pady=18)
 def macro_tab(s,n):
  t=tk.Frame(n,bg='#192433');n.add(t,text='Macros');tk.Label(t,text='Tap Macros',bg='#192433',fg='#eef6ff',font=('Segoe UI Semibold',14)).pack(anchor='w',padx=20,pady=(18,4));tk.Label(t,text='Create a global-hotkey macro that taps actions in order. Use comma-separated actions such as:  1, 2, tab, shift+w. Each action is pressed and released.',bg='#192433',fg='#9eb2c6',wraplength=820,justify='left').pack(anchor='w',padx=20,pady=(0,12))
  s.mn=tk.StringVar();s.mh=tk.StringVar();s.ma=tk.StringVar();s.md=tk.StringVar(value='0.10')
  for label,var,hint in [('Name',s.mn,'e.g. Countermeasures'),('Hotkey',s.mh,'e.g. ctrl+alt+1'),('Actions',s.ma,'e.g. 1, 2, tab'),('Delay between actions (seconds)',s.md,'e.g. 0.10')]:
   r=tk.Frame(t,bg='#192433');r.pack(fill='x',padx=20,pady=6);tk.Label(r,text=label,bg='#192433',fg='#eef6ff',width=28,anchor='w').pack(side='left');tk.Entry(r,textvariable=var,bg='#0f1721',fg='#eaf4ff',insertbackground='white',relief='flat',width=40).pack(side='left',ipady=5);tk.Label(r,text=hint,bg='#192433',fg='#8ca2b9').pack(side='left',padx=8)
  tk.Button(t,text='Add macro',command=s.add_macro,bg='#2a6f9e',fg='white',relief='flat',padx=16,pady=8).pack(anchor='w',padx=20,pady=(10,8));s.mlist=tk.Listbox(t,bg='#0f1721',fg='#d9eafa',selectbackground='#2a6f9e',relief='flat',height=7);s.mlist.pack(fill='both',expand=True,padx=20,pady=4);tk.Button(t,text='Remove selected macro',command=s.remove_macro,bg='#a65a46',fg='white',relief='flat',padx=14,pady=7).pack(anchor='w',padx=20,pady=(6,14));s.refresh_macros()
 def refresh_macros(s):
  s.mlist.delete(0,'end')
  for m in s.cfg['macros']:s.mlist.insert('end',f"{m['name']}  —  {m['hotkey']}  →  {m['actions']}")
 def add_macro(s):
  name=s.mn.get().strip();hot=s.mh.get().strip().lower();actions=s.ma.get().strip().lower()
  try:delay=float(s.md.get())
  except:delay=-1
  if not name or not hot or not actions or delay<0:messagebox.showerror('Macro details needed','Enter a name, hotkey, actions, and a delay of 0 or greater.');return
  s.cfg['macros'].append({'name':name,'hotkey':hot,'actions':actions,'delay':delay});s.mn.set('');s.mh.set('');s.ma.set('');s.refresh_macros();s.save();s.log('Macro added: '+name)
 def remove_macro(s):
  sel=s.mlist.curselection()
  if not sel:return
  name=s.cfg['macros'].pop(sel[0])['name'];s.refresh_macros();s.save();s.log('Macro removed: '+name)
 def update_json_indicator(s):
  s.json_status.set('Active settings: '+s.settings_source+'   •   Local file: '+F)
 def sync_entries(s):
  # Copy current screen values into the live configuration before saving or exporting.
  for k,v in s.vars.items():s.cfg[k]=int(v.get()) if k.endswith(('idle','interval')) else v.get().strip().lower()
 def save(s):
  try:
   s.sync_entries()
   with open(F,'w',encoding='utf8') as f:json.dump(s.cfg,f,indent=2)
   s.settings_source='Local settings.json (saved)';s.update_json_indicator();s.register();s.log('Settings saved and hotkeys updated.')
  except ValueError:messagebox.showerror('Invalid value','Idle and interval values must be whole numbers.')
  except Exception as e:messagebox.showerror('Could not save settings',str(e))
 def documents_folder(s):
  # Windows normally exposes this folder through USERPROFILE; create it if needed.
  folder=os.path.join(os.path.expanduser('~'),'Documents')
  os.makedirs(folder,exist_ok=True)
  return folder
 def backup_hotkeys(s):
  try:
   s.sync_entries()
   # Include all hotkeys, held-key choice, timing values, and macros so one file restores the setup.
   path=os.path.join(s.documents_folder(),'StarCitizenHelper_hotkeys_backup.json')
   with open(path,'w',encoding='utf8') as f:json.dump(s.cfg,f,indent=2)
   s.settings_source='Local settings.json • backup created: '+os.path.basename(path);s.update_json_indicator();s.log('Hotkey backup saved: '+path)
   messagebox.showinfo('Backup saved','Your hotkeys and macros were saved to:\n'+path)
  except ValueError:messagebox.showerror('Invalid value','Idle and interval values must be whole numbers.')
  except Exception as e:messagebox.showerror('Could not create backup',str(e))
 def import_hotkeys(s):
  path=filedialog.askopenfilename(title='Import Star Citizen Helper hotkeys',initialdir=s.documents_folder(),filetypes=[('JSON files','*.json'),('All files','*.*')])
  if not path:return
  try:
   with open(path,encoding='utf8') as f:data=json.load(f)
   if not isinstance(data,dict):raise ValueError('The selected JSON must contain a settings object.')
   imported={k:data[k] for k in D if k in data} # hold_stop from older backups is intentionally ignored
   if not imported:raise ValueError('No Star Citizen Helper settings were found in this JSON file.')
   if 'macros' in imported:
    if not isinstance(imported['macros'],list):raise ValueError('The macros value must be a list.')
    for m in imported['macros']:
     if not isinstance(m,dict) or not all(k in m for k in ('name','hotkey','actions')):raise ValueError('A macro entry is missing its name, hotkey, or actions.')
   for k in ('keepalive_idle','keepalive_interval','scan_interval'):
    if k in imported:imported[k]=int(imported[k])
   s.cfg.update(imported)
   for k,v in s.vars.items():v.set(str(s.cfg[k]))
   s.refresh_macros()
   with open(F,'w',encoding='utf8') as f:json.dump(s.cfg,f,indent=2)
   s.settings_source='Imported JSON: '+os.path.basename(path);s.update_json_indicator();s.register();s.log('Imported hotkeys and macros from: '+path)
   messagebox.showinfo('Import complete','Hotkeys and macros were imported and activated.')
  except (OSError,json.JSONDecodeError,ValueError) as e:messagebox.showerror('Could not import hotkeys',str(e))
  except Exception as e:messagebox.showerror('Could not import hotkeys',str(e))
 def register(s):
  for h in s.handles:
   try:keyboard.remove_hotkey(h)
   except:pass
  s.handles=[]
  try:
   for k,fn in [('keepalive_on',s.on_keep),('keepalive_off',s.off_keep),('scan_toggle',s.toggle_scan),('hold_start',s.toggle_hold)]:s.handles.append(keyboard.add_hotkey(s.cfg[k],fn,suppress=False))
   for m in s.cfg['macros']:s.handles.append(keyboard.add_hotkey(m['hotkey'],lambda x=m:s.run_macro(x),suppress=False))
  except Exception as e:s.q.put(str(e))
 def activity(s,*x):
  if time.monotonic()>=s.ignore:s.last=time.monotonic()
 def send(s,source):s.ignore=time.monotonic()+.15;keyboard.press_and_release('tab');s.q.put(source+': sent Tab')
 def on_keep(s):s.keep=True;s.q.put('Keepalive enabled.')
 def off_keep(s):s.keep=False;s.q.put('Keepalive disabled.')
 def toggle_scan(s):s.scan=not s.scan;s.nexts=time.monotonic();s.q.put('Ship Scan '+('enabled.' if s.scan else 'disabled.'))
 def run_macro(s,m):
  if s.running_macro:s.q.put('Macro ignored: another macro is running.');return
  threading.Thread(target=s._macro,args=(m,),daemon=True).start()
 def _macro(s,m):
  s.running_macro=m['name'];s.q.put('Macro started: '+m['name'])
  try:
   for action in m['actions'].split(','):
    action=action.strip()
    if action:keyboard.press_and_release(action);time.sleep(float(m.get('delay',.1)))
   s.q.put('Macro finished: '+m['name'])
  except Exception as e:s.q.put('Macro error ('+m['name']+'): '+str(e))
  finally:s.running_macro=''
 def toggle_hold(s):
  # One hotkey controls both states.  If it is active (or in the short arming
  # window), this press stops it; otherwise it arms the selected keys.
  if s.hold or s.hold_pending:
   s.release();s.q.put('KeepRunning toggled off.');return
  keys=[x.strip() for x in s.cfg['hold_keys'].split('+') if x.strip()]
  if not keys:s.q.put('No keys configured for KeepRunning.');return
  # Wait for Shift+W+Page Up to be physically released before pressing Shift+W.
  s.hold_pending=True;s.hold_token+=1;token=s.hold_token;s.q.put('KeepRunning arming: '+'+'.join(keys))
  threading.Thread(target=s._activate_hold,args=(keys,token),daemon=True).start()
 def _activate_hold(s,keys,token):
  time.sleep(.35)
  if s.stop.is_set() or token!=s.hold_token:return
  try:
   for x in keys:keyboard.press(x)
   s.held=keys;s.hold=True;s.hold_pending=False;s.q.put('KeepRunning toggled on: '+'+'.join(s.held))
  except Exception as e:
   s.held=[];s.hold=False;s.hold_pending=False;s.q.put('Could not hold keys: '+str(e))
 def release(s):
  # Also cancel an armed-but-not-yet-active hold.
  s.hold_token+=1;s.hold_pending=False
  for x in reversed(s.held):
   try:keyboard.release(x)
   except:pass
  if s.hold:s.q.put('KeepRunning released.')
  s.held=[];s.hold=False
 def emergency(s):
  s.release()
  for x in ('shift','ctrl','alt','win','w','a','s','d','tab'):
   try:keyboard.release(x)
   except:pass
  s.q.put('Emergency release sent.')
 def loop(s):
  while not s.stop.is_set():
   now=time.monotonic()
   if s.scan and now>=s.nexts:s.send('Ship Scan');s.nexts=now+max(1,int(s.cfg['scan_interval']))
   if s.keep and now-s.last>=max(1,int(s.cfg['keepalive_idle'])) and now>=s.nextk:s.send('Keepalive');s.nextk=now+max(1,int(s.cfg['keepalive_interval']))
   time.sleep(.05)
 def events(s):
  try:
   while 1:s.log(s.q.get_nowait())
  except queue.Empty:pass
  if not s.stop.is_set():s.after(100,s.events)
 def dashboard(s):
  def c(n,on,suf=''):s.chips[n].config(text=n+': '+('ON' if on else 'OFF')+suf,bg='#1f7852' if on else '#253448',fg='#effff5' if on else '#b6c5d5')
  c('Keepalive',s.keep);c('Ship Scan',s.scan);c('KeepRunning',s.hold or s.hold_pending,' ('+('+'.join(s.held) if s.hold else 'arming')+')' if (s.hold or s.hold_pending) else '');c('Macro',bool(s.running_macro),' ('+s.running_macro+')' if s.running_macro else '')
  text='Physical inactivity: '+str(int(time.monotonic()-s.last))+'s'
  if s.scan:text+='   •   Ship Scan Tab in '+format(max(0,s.nexts-time.monotonic()),'.1f')+'s'
  if s.keep:text+='   •   Keepalive armed'
  s.status.set(text)
  if not s.stop.is_set():s.after(200,s.dashboard)
 def log(s,x):s.out.config(state='normal');s.out.insert('end','['+time.strftime('%H:%M:%S')+'] '+x+'\n');s.out.see('end');s.out.config(state='disabled')
 def close(s):
  s.stop.set();s.scan=s.keep=False;s.emergency()
  try:keyboard.unhook_all_hotkeys();keyboard.unhook_all()
  except:pass
  if s.ml:
   try:s.ml.stop()
   except:pass
  s.destroy()
if __name__=='__main__':App().mainloop()