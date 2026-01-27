#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GM Console v7.1 - Dialog Input Fix
Fixes:
1. UI Bug: Fixed "New Script" dialog input label clipping. 
   Switched from Quasar Labels to Placeholders to fit the custom 32px height constraint.
"""

import asyncio
import json
import socket
import os
import sys
from datetime import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Any
from nicegui import ui, app

# Windows asyncio exception handler
def _windows_exception_handler(loop, context):
    exception = context.get('exception')
    if isinstance(exception, ConnectionResetError):
        return
    loop.default_exception_handler(context)

# ============================================================================
# Logic Components (Backend - Unchanged)
# ============================================================================

class CustomGmManager:
    def __init__(self):
        self.file_path = os.path.join(os.path.dirname(__file__), "custom_gm.json")
        self.commands = self.load()
    def load(self):
        if not os.path.exists(self.file_path): return []
        try:
            with open(self.file_path, 'r', encoding='utf-8') as f: return json.load(f)
        except: return []
    def save(self):
        try:
            with open(self.file_path, 'w', encoding='utf-8') as f:
                json.dump(self.commands, f, indent=2, ensure_ascii=False)
        except: pass
    def add(self, name, cmd):
        self.commands.append({"name": name, "cmd": cmd})
        self.save()
    def delete(self, index):
        if 0 <= index < len(self.commands):
            self.commands.pop(index)
            self.save()
    def edit(self, index, name, cmd):
        if 0 <= index < len(self.commands):
            self.commands[index] = {"name": name, "cmd": cmd}
            self.save()

custom_mgr = CustomGmManager()

@dataclass
class Client:
    id: str
    port: int
    writer: asyncio.StreamWriter
    device: str = "Unknown"
    platform: str = "Unknown"
    gm_tree: List[Any] = field(default_factory=list) 
    ui_states: Dict[str, Any] = field(default_factory=dict)

@dataclass
class Log:
    time: datetime
    level: str
    msg: str

class ServerMgr:
    def __init__(self):
        self.listeners = {} 
        self.clients = {}   
        self.logs = []
        self.cmd_id = 1000
        self.on_update = None 
        self.on_log = None
        self.on_client_data_update = None
    
    async def add_listener(self, port):
        if port in self.listeners: return False, f"Port {port} active"
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("0.0.0.0", port))
            sock.close()
        except Exception as e: return False, f"Bind Error: {e}"
        
        try:
            srv = await asyncio.start_server(lambda r,w: self._h(r,w,port), "0.0.0.0", port, reuse_address=True)
            self.listeners[port] = srv
            if self.on_update: self.on_update()
            return True, "Success"
        except Exception as e: return False, str(e)

    async def remove_listener(self, port):
        if port in self.listeners:
            s = self.listeners.pop(port)
            s.close()
            try: await s.wait_closed()
            except: pass
        
        to_remove = [cid for cid, c in self.clients.items() if c.port == port]
        for cid in to_remove:
            c = self.clients.pop(cid, None)
            if c:
                try: c.writer.close(); await c.writer.wait_closed()
                except: pass
        
        if self.on_update: self.on_update()

    async def _h(self, r, w, port):
        addr = w.get_extra_info("peername")
        cid = f"{addr[0]}:{addr[1]}"
        for ocid in [k for k,v in self.clients.items() if v.port == port]:
            oc = self.clients.pop(ocid)
            try: oc.writer.close()
            except: pass

        self.clients[cid] = Client(id=cid, port=port, writer=w)
        if self.on_update: self.on_update()
        
        try:
            while True:
                line = await r.readline()
                if not line: break
                try:
                    pkt = json.loads(line.decode().strip())
                    self._process(cid, pkt)
                except: pass
        except: pass
        finally:
            if cid in self.clients: del self.clients[cid]
            if self.on_update: self.on_update()

    def _process(self, cid, pkt):
        t = pkt.get("type")
        c = self.clients.get(cid)
        if not c: return

        if t == "HELLO":
            c.device = pkt.get("device","Unknown")
            c.platform = pkt.get("platform","Unknown")
            if self.on_update: self.on_update()
        elif t == "LOG":
            l = Log(datetime.now(), pkt.get("level","info"), pkt.get("msg",""))
            self.logs.append(l)
            if self.on_log: self.on_log(l)
        elif t == "GM_LIST":
            c.gm_tree = pkt.get("data", [])
            if self.on_client_data_update: self.on_client_data_update(cid)

    async def send_to_port(self, port, cmd):
        if port is None: 
            await self.broadcast(cmd)
            return True, "Broadcast Sent"
            
        client = next((c for c in self.clients.values() if c.port == port), None)
        if not client: return False, f"No device on Port {port}"
        
        try:
            data = json.dumps({"type":"EXEC","id":self.cmd_id,"cmd":cmd}, ensure_ascii=False)+"\n"
            client.writer.write(data.encode())
            await client.writer.drain()
            return True, f"Sent to {client.device}"
        except Exception as e: return False, str(e)

    async def send_gm_to_port(self, port, gm_id, val=None):
        if port is None:
            await self.broadcast_gm(gm_id, val)
            return True, "Broadcast GM Sent"

        client = next((c for c in self.clients.values() if c.port == port), None)
        if not client: return False, f"No device on Port {port}"
        
        if val is not None:
            client.ui_states[gm_id] = val
        
        try:
            data = json.dumps({"type":"EXEC_GM","id":gm_id,"value":val}, ensure_ascii=False)+"\n"
            client.writer.write(data.encode())
            await client.writer.drain()
            return True, f"GM Sent to {client.device}"
        except Exception as e: return False, str(e)

    async def broadcast(self, cmd):
        for cid, c in self.clients.items():
            try:
                c.writer.write((json.dumps({"type":"EXEC","id":self.cmd_id,"cmd":cmd}, ensure_ascii=False)+"\n").encode())
                await c.writer.drain()
            except: pass

    async def broadcast_gm(self, gm_id, val=None):
        for cid, c in self.clients.items():
            try:
                c.writer.write((json.dumps({"type":"EXEC_GM","id":gm_id,"value":val}, ensure_ascii=False)+"\n").encode())
                await c.writer.drain()
            except: pass

mgr = ServerMgr()
state = {"sel_port": None} 
ui_settings = {"custom_cols": 5} 

# ============================================================================
# UI Components
# ============================================================================
@ui.page('/')
def main():
    # --- CSS DESIGN SYSTEM ---
    ui.add_head_html('''
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500;700&family=Rajdhani:wght@500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            /* Theme: Obsidian (Dark / Matte / Technical) */
            --bg-base: #0a0a0c;       /* Near Black */
            --bg-surface: #131316;    /* Dark Matte Grey */
            --bg-highlight: #1c1c21;  /* Card surface */
            
            --border-subtle: rgba(255, 255, 255, 0.08);
            --border-strong: rgba(255, 255, 255, 0.15);
            
            /* The "Matrix" Dot Pattern */
            --pattern-color: rgba(255,255,255,0.03);
            
            /* Accent: Electric Cyan */
            --accent: #00e5ff;
            --accent-dim: rgba(0, 229, 255, 0.1);
            --accent-glow: rgba(0, 229, 255, 0.4);
            
            --text-pri: #ececf1;
            --text-sec: #8d8d96;
            
            --font-ui: 'Inter', sans-serif;
            --font-tech: 'Rajdhani', sans-serif; /* For headers */
            --font-mono: 'JetBrains Mono', monospace;
            
            --radius-sm: 4px;
            --radius-md: 8px;
        }

        body.theme-light {
            /* Theme: Frost (Light / Clean / Architectural) */
            --bg-base: #f4f5f7;
            --bg-surface: #ffffff;
            --bg-highlight: #ffffff;
            
            --border-subtle: rgba(0, 0, 0, 0.06);
            --border-strong: rgba(0, 0, 0, 0.12);
            
            --pattern-color: rgba(0,0,0,0.03);
            
            /* Accent: International Orange / Deep Slate */
            --accent: #2c3e50; 
            --accent-dim: rgba(44, 62, 80, 0.05);
            --accent-glow: rgba(44, 62, 80, 0.2);
            
            --text-pri: #1a1a1a;
            --text-sec: #64748b;
        }

        body { 
            font-family: var(--font-ui); 
            background-color: var(--bg-base); 
            color: var(--text-pri);
            margin: 0;
            /* High-end Dot Matrix Background */
            background-image: radial-gradient(var(--pattern-color) 1px, transparent 1px);
            background-size: 20px 20px;
            overflow: hidden;
            transition: all 0.3s ease;
        }

        /* --- Utility Classes --- */
        .glass-panel {
            background: var(--bg-surface);
            border: 1px solid var(--border-subtle);
            backdrop-filter: blur(10px);
            box-shadow: 0 4px 20px rgba(0,0,0,0.2);
        }
        
        .tech-font { font-family: var(--font-tech); letter-spacing: 0.05em; text-transform: uppercase; }
        .mono-font { font-family: var(--font-mono); }

        /* --- INPUTS: Recessed Look (Inset) --- */
        .input-slot {
            background-color: rgba(0,0,0,0.2); /* Darker than card */
            border: 1px solid var(--border-subtle);
            border-radius: var(--radius-sm);
            box-shadow: inset 0 2px 4px rgba(0,0,0,0.2); /* Inset shadow for depth */
            transition: all 0.2s;
        }
        body.theme-light .input-slot {
            background-color: #f1f2f6;
            box-shadow: inset 0 2px 4px rgba(0,0,0,0.05);
        }
        
        .input-slot:focus-within {
            border-color: var(--accent);
            background-color: rgba(0,0,0,0.3);
        }
        body.theme-light .input-slot:focus-within {
            background-color: #ffffff;
        }

        /* Q-Input Overrides for Visibility */
        .clean-input .q-field__control { height: 32px; min-height: 32px; }
        .clean-input .q-field__control:before, .clean-input .q-field__control:after { display: none; }
        .clean-input .q-field__native, .clean-input .q-field__input {
            color: var(--text-pri) !important; 
            font-family: var(--font-mono);
            font-size: 12px;
            caret-color: var(--accent);
        }
        .clean-textarea .q-field__native {
            color: var(--text-pri) !important;
            font-family: var(--font-mono);
            font-size: 13px;
            line-height: 1.5;
        }

        /* --- CARDS: Elevated "Matte" Look --- */
        .control-tile {
            /* Gradient for subtle sheen */
            background: linear-gradient(145deg, var(--bg-highlight) 0%, var(--bg-surface) 100%);
            border: 1px solid var(--border-subtle);
            border-radius: var(--radius-md);
            position: relative;
            overflow: hidden;
            transition: all 0.2s cubic-bezier(0.25, 0.8, 0.25, 1);
            cursor: pointer;
        }
        
        /* Subtle noise texture on cards */
        .control-tile::before {
            content: "";
            position: absolute;
            top: 0; left: 0; width: 100%; height: 100%;
            background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noiseFilter'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.8' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noiseFilter)' opacity='0.03'/%3E%3C/svg%3E");
            pointer-events: none;
            opacity: 0.5;
        }

        .control-tile:hover {
            transform: translateY(-2px);
            border-color: var(--text-sec);
            box-shadow: 0 8px 16px rgba(0,0,0,0.2);
        }
        
        .control-tile:active {
            transform: translateY(0);
            border-color: var(--accent);
        }

        /* Active State */
        .control-tile.active {
            border-color: var(--accent);
            box-shadow: 0 0 0 1px var(--accent), 0 0 20px var(--accent-dim);
            background: linear-gradient(145deg, var(--bg-surface) 0%, var(--accent-dim) 100%);
        }

        .tile-head {
            font-size: 11px;
            font-weight: 600;
            color: var(--text-pri);
            letter-spacing: 0.02em;
            line-height: 1.3;
            z-index: 1; position: relative;
        }
        .tile-meta {
            font-family: var(--font-mono);
            font-size: 9px;
            color: var(--text-sec);
            text-transform: uppercase;
            z-index: 1; position: relative;
        }

        /* --- BUTTONS --- */
        .btn-action {
            background-color: var(--accent);
            color: #000;
            font-weight: 700;
            border-radius: var(--radius-sm);
            letter-spacing: 0.05em;
            box-shadow: 0 0 10px var(--accent-dim);
            transition: 0.2s;
        }
        body.theme-light .btn-action { color: #fff; }
        
        .btn-action:hover {
            box-shadow: 0 0 15px var(--accent-glow);
            filter: brightness(1.1);
        }
        
        .btn-ghost {
            color: var(--text-sec);
        }
        .btn-ghost:hover { color: var(--text-pri); background: var(--bg-highlight); }

        ::-webkit-scrollbar { width: 4px; height: 4px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: var(--border-strong); border-radius: 2px; }
        ::-webkit-scrollbar-thumb:hover { background: var(--text-sec); }
    </style>
    ''')

    list_container = None
    log_container = None
    target_label = None
    
    refresh_gm_panel_callback = None 
    refresh_custom_panel_callback = None

    # --- UI UPDATE LOGIC ---
    def update_target_label():
        if not target_label: return
        p = state["sel_port"]
        if p is None:
            target_label.text = 'BROADCAST_LINK // ACTIVE'
            target_label.classes('text-[var(--accent)]', remove='text-amber-500 text-[var(--text-sec)]')
        else:
            client = next((c for c in mgr.clients.values() if c.port == p), None)
            if client:
                target_label.text = f'LINK_ESTABLISHED: {client.device.upper()} [:{p}]'
                target_label.classes('text-emerald-500', remove='text-[var(--text-sec)] text-[var(--accent)] text-amber-500')
            else:
                target_label.text = f'LINK_LOST: PORT_{p}'
                target_label.classes('text-amber-500', remove='text-[var(--text-sec)] text-[var(--accent)] text-emerald-500')

    def refresh_list():
        if not list_container: return
        list_container.clear()
        update_target_label()
        
        with list_container:
            # Broadcast Card
            is_all_sel = state["sel_port"] is None
            bc_classes = 'control-tile active' if is_all_sel else 'control-tile opacity-60'
            
            with ui.row().classes(f'w-full p-3 mb-3 {bc_classes} items-center justify-center gap-2').on('click', lambda: select_port(None)):
                ui.icon('hub', size='xs').classes('text-[var(--text-pri)]')
                ui.label('BROADCAST_MESH').classes('text-xs font-bold tech-font text-[var(--text-pri)]')

            # Active Ports
            active_ports = sorted(mgr.listeners.keys())
            for port in active_ports:
                connected_client = next((c for c in mgr.clients.values() if c.port == port), None)
                is_selected = (state["sel_port"] == port)
                card_cls = 'control-tile active' if is_selected else 'control-tile'
                
                with ui.column().classes(f'w-full p-0 mb-2 {card_cls} group').on('click', lambda p=port: select_port(p)):
                    # Header
                    with ui.row().classes('w-full justify-between items-center px-3 py-2 border-b border-[var(--border-subtle)] bg-[var(--bg-base)]/30'):
                        ui.label(f':{port}').classes('text-[10px] font-mono text-[var(--text-pri)] opacity-70')
                        
                        async def close_port(p=port):
                            await mgr.remove_listener(p)
                            if state["sel_port"] == p: 
                                state["sel_port"] = None
                                if refresh_gm_panel_callback: refresh_gm_panel_callback()
                            ui.notify(f'Terminated Port {p}', type='info')
                            refresh_list()
                        
                        ui.icon('power_settings_new', size='xs').classes('opacity-0 group-hover:opacity-100 cursor-pointer hover:text-red-500 transition-opacity text-[var(--text-sec)]').on('click.stop', close_port)

                    # Body
                    with ui.row().classes('w-full px-3 py-2 items-center gap-3'):
                        if connected_client:
                            ui.element('div').classes('w-1.5 h-1.5 rounded-full bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.8)]')
                            with ui.column().classes('gap-0 flex-1 min-w-0'):
                                ui.label(connected_client.device).classes('tile-head truncate')
                                ui.label(connected_client.platform).classes('tile-meta opacity-50')
                        else:
                            ui.element('div').classes('w-1.5 h-1.5 rounded-full bg-amber-500 animate-pulse')
                            ui.label('AWAITING_SIGNAL...').classes('text-[9px] font-bold text-amber-500 tech-font tracking-wider')

    def select_port(port):
        state["sel_port"] = port
        refresh_list()
        if refresh_gm_panel_callback: refresh_gm_panel_callback()

    mgr.on_update = refresh_list

    # ==========================================================================
    # Layout Structure
    # ==========================================================================
    
    # --- HEADER ---
    with ui.header().classes('h-[54px] glass-panel border-b-0 border-b-[var(--border-subtle)] px-6 flex items-center justify-between z-20').style('background: rgba(var(--bg-base), 0.8)'):
        with ui.row().classes('items-center gap-4'):
            with ui.row().classes('items-center gap-2'):
                ui.icon('token', size='sm').classes('text-[var(--accent)]')
                with ui.column().classes('gap-0'):
                    ui.label('GM_CONSOLE').classes('font-bold text-lg leading-none tech-font text-[var(--text-pri)]')
                    ui.label('VER 7.1 // INDUSTRIAL_CORE').classes('text-[9px] font-bold text-[var(--text-sec)] tracking-[0.2em]')
            
        with ui.row().classes('items-center gap-6'):
             with ui.row().classes('items-center gap-2 px-3 py-1 rounded-full border border-[var(--border-subtle)] bg-[var(--bg-base)]'):
                ui.element('div').classes('w-2 h-2 rounded-full bg-emerald-500 animate-pulse')
                ui.label().bind_text_from(mgr.listeners, lambda l: f'{len(l)} NODES ONLINE').classes('text-[10px] font-bold font-mono text-[var(--text-pri)]')
             
             ui.button(on_click=lambda: ui.run_javascript('document.body.classList.toggle("theme-light")'), icon='contrast').props('flat round dense size=sm').classes('btn-ghost')

    # --- BODY ---
    with ui.row().classes('w-full h-[calc(100vh-54px)] gap-0 no-wrap'):
        
        # --- LEFT SIDEBAR ---
        with ui.column().classes('w-[280px] h-full glass-panel border-l-0 border-y-0 p-5 flex-none overflow-y-auto'):
            ui.label('LISTENER_CONFIG').classes('text-[10px] font-bold text-[var(--text-sec)] mb-3 tech-font tracking-widest')
            
            with ui.row().classes('w-full mb-8 input-slot p-1'):
                port_input = ui.input(placeholder='PORT_ID').props('dense borderless input-class="text-center"').classes('flex-1 clean-input pl-2')
                port_input.value = '12581'
                async def handle_add():
                    val = port_input.value
                    if not val or not val.isdigit():
                        ui.notify('Invalid Port', type='warning'); return
                    p = int(val)
                    success, msg = await mgr.add_listener(p)
                    if success:
                        ui.notify(f'Listener {p} Initialized', type='positive')
                        port_input.value = str(p + 1)
                        refresh_list()
                    else:
                        ui.notify(msg, type='negative')
                ui.button(on_click=handle_add, icon='add').props('flat dense size=sm').classes('text-[var(--accent)] rounded hover:bg-[var(--bg-highlight)] w-[32px]')

            ui.label('NETWORK_GRID').classes('text-[10px] font-bold text-[var(--text-sec)] mb-3 tech-font tracking-widest')
            list_container = ui.column().classes('w-full gap-0')
            refresh_list()

        # --- CENTER STAGE ---
        with ui.column().classes('flex-1 h-full p-6 overflow-y-auto gap-6'):
            
            # Lua Terminal
            with ui.column().classes('control-tile w-full p-0 flex-none group'):
                with ui.row().classes('w-full justify-between items-center px-4 py-2 border-b border-[var(--border-subtle)] bg-[var(--bg-base)]/50'):
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('terminal', size='xs').classes('text-[var(--text-sec)]')
                        ui.label('LUA_EXEC_BUFFER').classes('text-[10px] font-bold text-[var(--text-pri)] tech-font tracking-wide')
                    target_label = ui.label('INIT...').classes('text-[10px] font-mono font-bold')
                
                with ui.column().classes('w-full p-4 relative'):
                    txt = ui.textarea(placeholder='// ENTER COMMAND STREAM...').classes('w-full clean-textarea input-slot rounded-md').props('borderless input-class="clean-textarea-input font-mono"').style('padding: 16px; min-height: 80px; resize: none;')
                    
                    with ui.row().classes('w-full justify-end gap-3 mt-3'):
                        ui.button('FLUSH_BUFFER', on_click=lambda: txt.set_value('')).props('flat dense size=xs').classes('text-[var(--text-sec)] font-mono hover:text-[var(--text-pri)]')
                        async def run_lua():
                            if not txt.value: return
                            success, msg = await mgr.send_to_port(state["sel_port"], txt.value)
                            if success: ui.notify('Command Executed', type='positive')
                            else: ui.notify(msg, type='warning')
                        ui.button('EXECUTE', on_click=run_lua, icon='play_arrow').props('unelevated dense size=sm').classes('btn-action px-4')

            # Main Controls
            with ui.column().classes('control-tile w-full flex-1 min-h-[400px] overflow-hidden'):
                with ui.tabs().classes('w-full text-[var(--text-sec)] border-b border-[var(--border-subtle)] bg-[var(--bg-base)]/50').props('dense active-color="accent" indicator-color="accent" align="left"') as tabs:
                    ui.tab('LuaGM', label='LuaGM').classes('font-bold tech-font tracking-wider text-xs px-6')
                    ui.tab('CustomGM', label='CustomGM').classes('font-bold tech-font tracking-wider text-xs px-6')
                    ui.space()
                    
                    with ui.row().classes('items-center gap-3 mr-6'):
                        ui.icon('grid_view', size='xs').classes('text-[var(--text-sec)]')
                        def on_slider_change(e):
                            ui_settings['custom_cols'] = e.value
                            if refresh_gm_panel_callback: refresh_gm_panel_callback()
                            if refresh_custom_panel_callback: refresh_custom_panel_callback()
                        ui.slider(min=2, max=8, step=1, value=5, on_change=on_slider_change).props('dense size=xs color=grey').classes('w-24').bind_value(ui_settings, 'custom_cols')
                        
                        ui.separator().props('vertical').classes('h-4 mx-2 border-[var(--border-subtle)]')

                        async def reload_gm():
                            c = "RuntimeGMClient.ReloadGM(true)"; 
                            await mgr.send_to_port(state["sel_port"], c)
                            ui.notify("Sync Signal Sent")
                        ui.button(icon='sync', on_click=reload_gm).props('flat round dense size=sm').classes('text-[var(--text-sec)] hover:text-[var(--accent)]')

                with ui.tab_panels(tabs, value='LuaGM').classes('w-full flex-1 bg-transparent p-5'):
                    
                    # --- HARMONIZED RENDERER ---
                    with ui.tab_panel('LuaGM').classes('p-0 h-full flex flex-col'):
                        gm_area = ui.column().classes('w-full gap-4')
                        class GMExplorer:
                            def __init__(self):
                                self.root = []; self.path = []; self.search = ""; self.client_context = None
                            def load_context(self):
                                p = state["sel_port"]
                                gm_area.clear()
                                if p is None:
                                    with gm_area: ui.label("SELECT TARGET NODE TO INITIALIZE UPLINK").classes('w-full text-center text-[var(--text-sec)] italic py-12 font-mono text-xs opacity-50')
                                    return
                                client = next((c for c in mgr.clients.values() if c.port == p), None)
                                self.client_context = client
                                if not client:
                                    with gm_area:
                                        with ui.column().classes('w-full items-center justify-center py-12 opacity-30 gap-3'):
                                            ui.icon('cable', size='xl').classes('text-[var(--text-sec)]')
                                            ui.label("ESTABLISHING HANDSHAKE...").classes('text-[var(--text-sec)] font-bold tech-font')
                                    return
                                self.root = client.gm_tree
                                if not self.root:
                                    with gm_area: ui.label("SYNCING DATA PACKETS...").classes('w-full text-center text-[var(--text-sec)] py-12 font-mono text-xs animate-pulse')
                                else: self.render()
                            
                            def nav(self, idx): self.path = [] if idx == -1 else self.path[:idx+1]; self.render()
                            def enter(self, node): self.path.append(node); self.render()
                            
                            def render(self):
                                gm_area.clear()
                                with gm_area:
                                    # Breadcrumb
                                    with ui.row().classes('w-full items-center input-slot px-3 py-1.5 gap-2'):
                                        ui.button(icon='home', on_click=lambda: self.nav(-1)).props('flat dense round size=xs').classes('text-[var(--text-sec)] hover:text-[var(--text-pri)]')
                                        for i, n in enumerate(self.path):
                                            ui.icon('chevron_right', size='xs').classes('text-[var(--text-sec)] opacity-40')
                                            ui.button(n['name'], on_click=lambda x=i: self.nav(x)).props('flat dense no-caps size=sm').classes('text-[var(--accent)] font-mono text-xs font-bold hover:underline')
                                        ui.space()
                                        ui.input(placeholder='FILTER_CMD').props('dense borderless input-class="text-[var(--text-pri)] text-right font-mono text-xs"').classes('clean-input w-40').bind_value(self, 'search').on('input', self.render)
                                    
                                    # Grid
                                    nodes = self.path[-1]['children'] if self.path else self.root
                                    if self.search:
                                        res = []; 
                                        def find(ns):
                                            for n in ns:
                                                if self.search.lower() in n.get('name','').lower(): res.append(n)
                                                if 'children' in n: find(n['children'])
                                        find(self.root); nodes = res
                                    
                                    cols = ui_settings['custom_cols']
                                    with ui.grid().classes('w-full gap-3').style(f'grid-template-columns: repeat({cols}, minmax(0, 1fr))'):
                                        for n in nodes:
                                            typ, name, nid = n.get('type'), n.get('name'), n.get('id')
                                            
                                            # --- UNIFIED TILE STYLING ---
                                            
                                            if typ == 'Toggle':
                                                async def tgl(e, i=nid):
                                                    await mgr.send_gm_to_port(state["sel_port"], i, e.value)
                                                initial_val = self.client_context.ui_states.get(nid, False)
                                                
                                                with ui.card().classes('control-tile p-3 h-24 flex flex-col justify-between'):
                                                    with ui.row().classes('w-full justify-between items-start no-wrap'):
                                                        ui.label(name).classes('tile-head leading-tight break-words pr-2')
                                                        ui.switch(value=initial_val, on_change=tgl).props('dense size=xs color=cyan').classes('min-w-[30px]')
                                                    ui.label('SWITCH_STATE').classes('tile-meta mt-auto self-start opacity-50')
                                            
                                            elif typ == 'Input':
                                                async def inp(e, i=nid):
                                                    await mgr.send_gm_to_port(state["sel_port"], i, e.value)
                                                initial_val = self.client_context.ui_states.get(nid, "")
                                                
                                                with ui.card().classes('control-tile p-3 h-24 flex flex-col justify-between gap-2'):
                                                    ui.label(name).classes('tile-head truncate w-full')
                                                    ui.input(value=initial_val, on_change=inp).props('dense borderless input-class="text-xs text-center font-mono"').classes('w-full input-slot clean-input')
                                            
                                            elif typ == 'Btn':
                                                async def clk(i=nid):
                                                    await mgr.send_gm_to_port(state["sel_port"], i)
                                                    ui.notify(f'Triggered: {name}')
                                                
                                                with ui.card().classes('control-tile p-3 h-24 flex flex-col justify-between group').on('click', clk):
                                                    ui.label(name).classes('tile-head leading-tight group-hover:text-[var(--accent)] transition-colors')
                                                    with ui.row().classes('w-full justify-between items-end mt-auto'):
                                                        ui.label('EXEC_CMD').classes('tile-meta opacity-40')
                                                        ui.icon('touch_app', size='xs').classes('text-[var(--text-sec)] group-hover:text-[var(--accent)] transition-colors')
                                            
                                            elif typ == 'SubBox':
                                                with ui.card().classes('control-tile p-3 h-24 flex flex-col justify-center items-center gap-2 group').on('click', lambda x=n: self.enter(x)):
                                                    ui.icon('folder_open', size='sm').classes('text-[var(--text-sec)] group-hover:text-[var(--accent)] transition-colors')
                                                    ui.label(name).classes('tile-head text-center group-hover:text-[var(--text-pri)]')
                        
                        explorer = GMExplorer()
                        def refresh_gm_proxy(): explorer.load_context()
                        refresh_gm_panel_callback = refresh_gm_proxy
                        mgr.on_client_data_update = lambda cid: refresh_gm_proxy() if state["sel_port"] else None
                        explorer.load_context()

                    with ui.tab_panel('CustomGM').classes('p-0'):
                        with ui.row().classes('w-full mb-3 justify-between items-center'):
                            ui.button('NEW SCRIPT', icon='add', on_click=lambda: add_dlg.open()).props('unelevated dense size=sm').classes('btn-action px-3 text-xs')
                            
                            # --- FIXED DIALOG INPUTS ---
                            with ui.dialog() as add_dlg, ui.card().classes('w-96 p-4 gap-4 glass-panel border border-[var(--border-subtle)]'):
                                ui.label('NEW PROTOCOL').classes('font-bold text-[var(--text-pri)] tech-font')
                                # FIX: Removed first argument (Label) and used placeholder prop instead
                                n_in = ui.input(placeholder='PROTOCOL_NAME').props('dense borderless').classes('w-full clean-input input-slot px-2')
                                c_in = ui.textarea(placeholder='PAYLOAD_LUA').props('borderless').classes('w-full clean-textarea input-slot px-2')
                                ui.button('SAVE', on_click=lambda: (custom_mgr.add(n_in.value, c_in.value), add_dlg.close(), r_cust())).classes('btn-action w-full')
                            
                            # --- EDIT DIALOG ---
                            edit_idx = [0]
                            with ui.dialog() as edit_dlg, ui.card().classes('w-96 p-4 gap-4 glass-panel border border-[var(--border-subtle)]'):
                                ui.label('EDIT PROTOCOL').classes('font-bold text-[var(--text-pri)] tech-font')
                                e_n_in = ui.input(placeholder='PROTOCOL_NAME').props('dense borderless').classes('w-full clean-input input-slot px-2')
                                e_c_in = ui.textarea(placeholder='PAYLOAD_LUA').props('borderless').classes('w-full clean-textarea input-slot px-2')
                                with ui.row().classes('w-full gap-2'):
                                    ui.button('SAVE', on_click=lambda: (custom_mgr.edit(edit_idx[0], e_n_in.value, e_c_in.value), edit_dlg.close(), r_cust())).classes('btn-action flex-1')
                                    ui.button('CANCEL', on_click=lambda: edit_dlg.close()).classes('btn-action flex-1')
                        
                        c_grid = ui.grid().classes('w-full gap-3')
                        def r_cust():
                            c_grid.clear()
                            cols = ui_settings['custom_cols']
                            c_grid.style(f'grid-template-columns: repeat({cols}, minmax(0, 1fr))')
                            with c_grid:
                                for idx, item in enumerate(custom_mgr.commands):
                                    with ui.card().classes('control-tile p-3 h-24 flex flex-col justify-between group'):
                                        async def run_c(c=item['cmd'], name=item['name']):
                                            success, msg = await mgr.send_to_port(state["sel_port"], c)
                                            if success: ui.notify(f"Sent: {name}")
                                        with ui.column().classes('w-full h-full cursor-pointer justify-between gap-1').on('click', run_c):
                                            ui.label(item['name']).classes('tile-head line-clamp-2')
                                            ui.label(item['cmd']).classes('tile-meta font-mono truncate w-full opacity-60')
                                        def open_edit(current_item=item, current_idx=idx):
                                            e_n_in.set_value(current_item['name'])
                                            e_c_in.set_value(current_item['cmd'])
                                            edit_idx[0] = current_idx
                                            edit_dlg.open()
                                        def open_delete(current_idx=idx):
                                            custom_mgr.delete(current_idx)
                                            r_cust()
                                        with ui.row().classes('absolute top-1 right-1 gap-1 opacity-0 group-hover:opacity-100'):
                                            ui.button(icon='edit', on_click=open_edit).props('flat dense round size=xs color-amber').classes('hover:text-amber-300')
                                            ui.button(icon='close', on_click=open_delete).props('flat dense round size=xs color-red').classes('hover:text-red-400')
                        
                        refresh_custom_panel_callback = r_cust
                        r_cust()

        # --- RIGHT SIDEBAR (LOGS) ---
        with ui.column().classes('w-[280px] h-full glass-panel border-r-0 border-y-0 flex-none flex flex-col'):
            with ui.row().classes('h-[42px] px-3 items-center justify-between border-b border-[var(--border-subtle)] bg-[var(--bg-base)]/30 w-full'):
                ui.label('SYSTEM_LOGS').classes('text-[10px] font-bold text-[var(--text-sec)] tech-font tracking-widest')
                ui.button(icon='delete_outline', on_click=lambda: log_container.clear()).props('flat dense round size=xs').classes('text-[var(--text-sec)] hover:text-red-400')
            log_container = ui.column().classes('w-full flex-1 overflow-y-auto gap-0 p-0')

async def startup():
    if sys.platform == 'win32': asyncio.get_running_loop().set_exception_handler(_windows_exception_handler)
    await mgr.add_listener(12581)

async def cleanup():
    for port in list(mgr.listeners.keys()): await mgr.remove_listener(port)

app.on_startup(startup)
app.on_shutdown(cleanup)

def kill_web_ui_port(port):
    try:
        import subprocess
        r = subprocess.check_output(f'netstat -ano | findstr :{port}', shell=True).decode()
        for pid in {line.split()[-1] for line in r.splitlines() if 'LISTENING' in line}:
            if pid != str(os.getpid()): os.system(f'taskkill /F /PID {pid} >nul 2>&1')
    except: pass

if __name__ in {"__main__", "__mp_main__"}:
    kill_web_ui_port(9529)
    ui.run(title="GM Core 7.1", host="0.0.0.0", port=9529, reload=False, favicon='💠')