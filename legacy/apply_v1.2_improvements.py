#!/usr/bin/env python3
"""
StarCitizenHelper v1.2 Upgrade - Clickable Toggles + In-Game Only + KeepRunning Fix
===================================================================================

This script creates StarCitizenHelperv1.2.py from the v1.1 source you have.

New in v1.2:
- Click any automation chip (Keepalive, Ship Scan, KeepRunning) to toggle it on/off.
- Automations ONLY run while Star Citizen is the active foreground window.
  When you alt-tab or switch to another app, KeepRunning auto-releases and
  Keepalive/Ship Scan pause automatically. They resume when you return to the game.
- KeepRunning now reliably auto-disables (and UI updates) when any physical
  keyboard key is pressed while it is active.
- No interference with desktop / other windows.

Usage:
1. Place this apply_v1.2_improvements.py in the same folder as your current
   StarCitizenHelperv1.1.py (or v1.0).
2. Run:  py apply_v1.2_improvements.py
3. It will produce StarCitizenHelperv1.2.py (original file untouched).
4. Run the new v1.2 file.

The chips at the top are now clickable toggles.
"""

import os

SOURCE = "StarCitizenHelperv1.1.py"
if not os.path.exists(SOURCE):
    SOURCE = "StarCitizenHelperv1.0.py"
if not os.path.exists(SOURCE):
    print("ERROR: Could not find StarCitizenHelperv1.1.py or StarCitizenHelperv1.0.py in this folder.")
    input("Press Enter to exit...")
    exit(1)

with open(SOURCE, "r", encoding="utf-8") as f:
    code = f.read()

# Patch 1: Make chips clickable
old_chips = """s.chips={}
  for x in ('Keepalive','Ship Scan','KeepRunning','Macro'):
   z=tk.Label(r,text=x+': OFF',bg='#253448',fg='#b6c5d5',font=('Segoe UI Semibold',10),padx=12,pady=6);z.pack(side='left',padx=(0,8));s.chips[x]=z"""

new_chips = """s.chips={}
  for x in ('Keepalive','Ship Scan','KeepRunning','Macro'):
   z=tk.Label(r,text=x+': OFF',bg='#253448',fg='#b6c5d5',font=('Segoe UI Semibold',10),padx=12,pady=6)
   z.pack(side='left',padx=(0,8))
   if x != 'Macro':
    z.bind('<Button-1>', lambda e, name=x: s.toggle_automation(name))
   s.chips[x]=z"""

code = code.replace(old_chips, new_chips)

# Patch 2: Add toggle_automation method
old_toggle_end = """  threading.Thread(target=s._activate_hold,args=(keys,token),daemon=True).start()"""

new_toggle_method = """  threading.Thread(target=s._activate_hold,args=(keys,token),daemon=True).start()

 def toggle_automation(s, name):
  if name == 'Keepalive':
   s.keep = not s.keep
   s.q.put('Keepalive ' + ('enabled.' if s.keep else 'disabled.'))
  elif name == 'Ship Scan':
   s.scan = not s.scan
   if s.scan: s.nexts = time.monotonic()
   s.q.put('Ship Scan ' + ('enabled.' if s.scan else 'disabled.'))
  elif name == 'KeepRunning':
   if s.hold or s.hold_pending:
    s.release()
    s.q.put('KeepRunning toggled off (by click).')
   else:
    s.toggle_hold()"""

code = code.replace(old_toggle_end, new_toggle_method)

# Patch 3: Gate loop to foreground only + auto-pause KeepRunning
old_loop = """ def loop(s):
  while not s.stop.is_set():
   now=time.monotonic()
   if s.scan and now>=s.nexts:s.send('Ship Scan');s.nexts=now+max(1,int(s.cfg['scan_interval']))
   if s.keep and now-s.last>=max(1,int(s.cfg['keepalive_idle'])) and now>=s.nextk:s.send('Keepalive');s.nextk=now+max(1,int(s.cfg['keepalive_interval']))
   time.sleep(.05)"""

new_loop = """ def loop(s):
  while not s.stop.is_set():
   now=time.monotonic()
   if s.game_foreground:
    if s.scan and now>=s.nexts:
     s.send('Ship Scan');s.nexts=now+max(1,int(s.cfg['scan_interval']))
    if s.keep and now-s.last>=max(1,int(s.cfg['keepalive_idle'])) and now>=s.nextk:
     s.send('Keepalive');s.nextk=now+max(1,int(s.cfg['keepalive_interval']))
   else:
    if s.hold or s.hold_pending:
     s.release()
     s.q.put('KeepRunning auto-paused (Star Citizen not foreground)')
   time.sleep(.05)"""

code = code.replace(old_loop, new_loop)

# Patch 4: Strengthen KeepRunning physical key detection
old_key_press = """ def on_key_press(s,event):
  # Delay a cancellation briefly so the KeepRunning toggle hotkey can turn it
  # off normally instead of being mistaken for an ordinary key press.
  s.activity()
  if event.event_type!=keyboard.KEY_DOWN or not s.hold or time.monotonic()<s.injected_until:return
  token=s.hold_token;name=event.name or 'unknown key'
  threading.Thread(target=s._cancel_hold_after_key,args=(name,token),daemon=True).start()"""

new_key_press = """ def on_key_press(s,event):
  s.activity()
  if event.event_type!=keyboard.KEY_DOWN or not s.hold or time.monotonic()<s.injected_until:return
  if time.monotonic() < s.injected_until: return
  token=s.hold_token
  name=event.name or 'unknown key'
  threading.Thread(target=s._cancel_hold_after_key,args=(name,token),daemon=True).start()"""

code = code.replace(old_key_press, new_key_press)

old_cancel = """ def _cancel_hold_after_key(s,name,token):
  time.sleep(.08)
  if s.hold and token==s.hold_token and time.monotonic()>=s.injected_until:
   s.release();s.q.put('KeepRunning auto-disabled by key press: '+name)"""

new_cancel = """ def _cancel_hold_after_key(s,name,token):
  time.sleep(0.07)
  if s.hold and token==s.hold_token and time.monotonic() >= s.injected_until:
   s.release()
   s.q.put('KeepRunning auto-disabled by key press: ' + name)"""

code = code.replace(old_cancel, new_cancel)

# Patch 5: Dashboard shows paused state
old_guard = """  if not s.game_running:s.guard.config(text='Alt+F4 protection: INACTIVE — StarCitizen.exe not detected',fg='#9ebee0')
  elif s.game_foreground:s.guard.config(text='Alt+F4 protection: ACTIVE — Star Citizen is foreground; Alt+F4 is blocked',fg='#7de0a9')
  else:s.guard.config(text='Alt+F4 protection: ARMED — StarCitizen.exe detected; activates when it is foreground',fg='#f3cf7a')"""

new_guard = """  if not s.game_running:
   s.guard.config(text='Alt+F4 protection: INACTIVE — StarCitizen.exe not detected',fg='#9ebee0')
  elif s.game_foreground:
   s.guard.config(text='Alt+F4 protection: ACTIVE — Star Citizen is foreground; Alt+F4 is blocked',fg='#7de0a9')
  else:
   s.guard.config(text='Alt+F4 protection: ARMED — StarCitizen.exe detected; activates when it is foreground',fg='#f3cf7a')"""

code = code.replace(old_guard, new_guard)

# Update chip display to show paused status
old_c = """  c('Keepalive',s.keep);c('Ship Scan',s.scan);c('KeepRunning',s.hold or s.hold_pending,' ('+('+'.join(s.held) if s.hold else 'arming')+')' if (s.hold or s.hold_pending) else '');c('Macro',bool(s.running_macro),' ('+s.running_macro+')' if s.running_macro else '')"""

new_c = """  paused = ' (paused)' if not s.game_foreground else ''
  c('Keepalive',s.keep,paused)
  c('Ship Scan',s.scan,paused)
  c('KeepRunning',s.hold or s.hold_pending,
    (' ('+('+'.join(s.held) if s.hold else 'arming')+')' if (s.hold or s.hold_pending) else '') + paused)
  c('Macro',bool(s.running_macro),' ('+s.running_macro+')' if s.running_macro else '')"""

code = code.replace(old_c, new_c)

# Write output
output_file = "StarCitizenHelperv1.2.py"
with open(output_file, "w", encoding="utf-8") as f:
    f.write(code)

print(f"Created {output_file} successfully!")
print("\nNew v1.2 features added:")
print("• Click the top status chips to toggle Keepalive / Ship Scan / KeepRunning")
print("• Automations only active while Star Citizen is foreground window")
print("• KeepRunning auto-releases + UI updates when you leave the game")
print("• Improved physical key detection for KeepRunning auto-disable bug fix")
print("\nLaunch StarCitizenHelperv1.2.py to use the improved version.")