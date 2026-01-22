#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GM Console v5.12 - Context Switch Fix
Fixes:
1. Critical Bug: Fixed UI not clearing/refreshing when switching between Connected and Waiting ports.
   (Removed faulty optimization in refresh_gm_proxy that prevented context reloading).
2. Verified: Button states (Toggles) are correctly restored per-client.
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
# Logic Components (Backend)
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

custom_mgr = CustomGmManager()

@dataclass
class Client:
    id: str
    port: int
    writer: asyncio.StreamWriter
    device: str = "Unknown"
    platform: str = "Unknown"
    # Per-Client Data Storage
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
    ui.add_head_html('''
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
    <style>
        body { font-family: 'Inter', sans-serif; background-color: #F8FAFC; color: #334155; }
        .mono { font-family: 'JetBrains Mono', monospace; }
        ::-webkit-scrollbar { width: 6px; height: 6px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: #CBD5E1; border-radius: 3px; }
        .dark-input input::placeholder { color: #64748B !important; opacity: 1; }
        .dark-input input { color: white !important; }
        @keyframes pulse-yellow {
            0% { box-shadow: 0 0 0 0 rgba(234, 179, 8, 0.4); }
            70% { box-shadow: 0 0 0 6px rgba(234, 179, 8, 0); }
            100% { box-shadow: 0 0 0 0 rgba(234, 179, 8, 0); }
        }
        .status-waiting { animation: pulse-yellow 2s infinite; }
    </style>
    ''')

    list_container = None
    log_container = None
    target_label = None
    
    # Refresh callbacks (proxies)
    refresh_gm_panel_callback = None 
    refresh_custom_panel_callback = None

    # --- UI UPDATE LOGIC ---
    def update_target_label():
        if not target_label: return
        p = state["sel_port"]
        if p is None:
            target_label.text = 'Target: Broadcast (All)'
            target_label.classes('text-slate-400 font-medium', remove='text-blue-600 text-red-500 font-bold')
        else:
            client = next((c for c in mgr.clients.values() if c.port == p), None)
            if client:
                target_label.text = f'Target: {client.device} (:{p})'
                target_label.classes('text-blue-600 font-bold', remove='text-slate-400 text-red-500 font-medium')
            else:
                target_label.text = f'Target: Port {p} (No Device)'
                target_label.classes('text-red-500 font-bold', remove='text-slate-400 text-blue-600 font-medium')

    def refresh_list():
        if not list_container: return
        list_container.clear()
        update_target_label()
        
        with list_container:
            # Broadcast Grid
            is_all_sel = state["sel_port"] is None
            all_bg = 'bg-blue-600 border-blue-500' if is_all_sel else 'bg-slate-800 border-slate-700 hover:border-slate-500'
            all_txt = 'text-white' if is_all_sel else 'text-slate-400'
            
            with ui.card().classes(f'w-full p-2 mb-3 {all_bg} border cursor-pointer transition-all').on('click', lambda: select_port(None)):
                with ui.row().classes('items-center gap-2 justify-center w-full'):
                    ui.icon('rss_feed', size='xs').classes(all_txt)
                    ui.label('Broadcast (All Ports)').classes(f'{all_txt} text-xs font-bold uppercase')

            # Active Ports
            active_ports = sorted(mgr.listeners.keys())
            for port in active_ports:
                connected_client = next((c for c in mgr.clients.values() if c.port == port), None)
                is_selected = (state["sel_port"] == port)
                
                if is_selected:
                    bg_cls = 'bg-blue-900/40 border-blue-500 border-2' 
                    header_bg = 'bg-blue-600/20'
                    txt_main = 'text-white'
                    txt_sub = 'text-blue-200'
                else:
                    bg_cls = 'bg-slate-800 border-slate-700 hover:border-slate-600'
                    header_bg = 'bg-black/10'
                    txt_main = 'text-slate-200'
                    txt_sub = 'text-slate-400'

                with ui.card().classes(f'w-full p-0 mb-3 {bg_cls} border transition-all relative group cursor-pointer').on('click', lambda p=port: select_port(p)) as card:
                    with ui.row().classes(f'w-full justify-between items-center px-3 py-2 {header_bg}'):
                        with ui.row().classes('items-center gap-2'):
                            ui.icon('dns', size='xs').classes('text-slate-400')
                            ui.label(f':{port}').classes(f'{txt_main} font-mono text-sm font-bold')
                        
                        async def close_port(p=port):
                            await mgr.remove_listener(p)
                            if state["sel_port"] == p: 
                                state["sel_port"] = None
                                if refresh_gm_panel_callback: refresh_gm_panel_callback()
                            ui.notify(f'Port {p} Closed', type='info')
                            refresh_list()
                        
                        ui.button(on_click=close_port, icon='close').props('dense flat size=xs round color=red').classes('hover:bg-red-900/50').on('click.stop', lambda: None)

                    with ui.column().classes('w-full px-3 py-2 gap-1'):
                        if connected_client:
                            with ui.row().classes('items-center gap-2 w-full'):
                                ui.element('div').classes('w-2 h-2 rounded-full bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.6)]')
                                with ui.column().classes('gap-0 flex-1 min-w-0'):
                                    ui.label(connected_client.device).classes(f'{txt_main} text-xs font-bold truncate')
                                    ui.label(connected_client.platform).classes(f'{txt_sub} text-[10px]')
                        else:
                            with ui.row().classes('items-center gap-2 w-full opacity-80'):
                                ui.element('div').classes('w-2 h-2 rounded-full bg-yellow-500 status-waiting')
                                ui.label('Waiting...').classes('text-yellow-500 text-xs italic font-medium')

    def select_port(port):
        state["sel_port"] = port
        refresh_list()
        if refresh_gm_panel_callback: refresh_gm_panel_callback()

    mgr.on_update = refresh_list

    # ==========================================================================
    # Layout
    # ==========================================================================
    with ui.header().classes('h-[50px] bg-white border-b border-slate-200 px-4 flex items-center justify-between z-20 shadow-sm'):
        with ui.row().classes('items-center gap-3'):
            ui.icon('terminal', size='sm').classes('text-slate-800')
            with ui.column().classes('gap-0'):
                ui.label('GM Console').classes('text-slate-800 font-bold text-sm leading-none')
                ui.label('Enterprise Edition').classes('text-slate-500 text-[10px] font-medium')
        with ui.row().classes('items-center gap-4'):
             with ui.row().classes('bg-slate-100 rounded-full px-3 py-1 items-center gap-3 border border-slate-200'):
                ui.label('Active Ports').classes('text-[10px] text-slate-500 uppercase font-bold')
                ui.label().bind_text_from(mgr.listeners, lambda l: str(len(l))).classes('text-xs font-bold text-slate-700')

    with ui.row().classes('w-full h-[calc(100vh-50px)] gap-0 no-wrap'):
        # --- Sidebar ---
        with ui.column().classes('w-[260px] h-full bg-[#0F172A] p-4 flex-none overflow-y-auto border-r border-slate-800 shadow-inner'):
            ui.label('ADD LISTENER').classes('text-slate-500 text-[10px] font-bold tracking-wider mb-2')
            with ui.row().classes('w-full gap-2 mb-6 items-center'):
                port_input = ui.input(placeholder='Port').props('dense outlined input-class="text-white"').classes('flex-1 dark-input bg-slate-800/50 rounded').style('font-size: 13px;')
                port_input.value = '12581'
                
                async def handle_add():
                    val = port_input.value
                    if not val or not val.isdigit():
                        ui.notify('Invalid Port', type='warning'); return
                    p = int(val)
                    
                    success, msg = await mgr.add_listener(p)
                    if success:
                        ui.notify(f'Port {p} Added', type='positive')
                        port_input.value = str(p + 1)
                        refresh_list()
                    else:
                        ui.notify(msg, type='negative')

                ui.button(on_click=handle_add, icon='add').props('dense flat color=white').classes('min-w-[32px] px-0 hover:bg-blue-600 rounded transition-colors')

            ui.label('LISTENER GRIDS').classes('text-slate-500 text-[10px] font-bold tracking-wider mb-2')
            list_container = ui.column().classes('w-full gap-0')
            refresh_list()

        # --- Main Area ---
        with ui.column().classes('flex-1 h-full bg-[#F8FAFC] p-6 overflow-y-auto gap-6'):
            # Lua Executor
            with ui.column().classes('ent-card w-full p-0 overflow-hidden'):
                with ui.row().classes('bg-slate-50 border-b border-slate-200 px-4 py-2 justify-between items-center w-full'):
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('code', size='xs').classes('text-slate-400')
                        ui.label('LUA EXECUTOR').classes('text-xs font-bold text-slate-700')
                    target_label = ui.label('Target: Broadcast (All)').classes('text-xs text-slate-400 font-medium')
                
                with ui.column().classes('w-full p-4 gap-3 bg-white'):
                    txt = ui.textarea(placeholder='-- Lua Code').classes('w-full').props('borderless input-class="mono text-sm"').style('background:#FAFAFA; border:1px solid #E2E8F0; border-radius:6px; padding:12px;')
                    with ui.row().classes('w-full justify-end gap-2'):
                        ui.button('Clear', on_click=lambda: txt.set_value('')).props('flat dense color=grey size=sm')
                        async def run_lua():
                            if not txt.value: return
                            success, msg = await mgr.send_to_port(state["sel_port"], txt.value)
                            if success: ui.notify(msg, type='positive')
                            else: ui.notify(msg, type='warning')
                        ui.button('Run', on_click=run_lua, icon='play_arrow').props('unelevated dense color=primary size=sm')

            # Tabs & GM Area
            with ui.column().classes('ent-card w-full flex-1 min-h-[400px] overflow-hidden'):
                
                # --- GLOBAL TAB HEADER ---
                with ui.tabs().classes('w-full text-slate-600 bg-white border-b border-slate-200').props('dense active-color="primary" indicator-color="primary"') as tabs:
                    ui.tab('LuaGM', label='Game Commands')
                    ui.tab('CustomGM', label='Custom Scripts')
                    ui.space() # Push to right
                    
                    # --- NEW GLOBAL DENSITY SLIDER ---
                    with ui.row().classes('items-center gap-2 mr-6'):
                        ui.icon('grid_view', size='xs').classes('text-slate-400')
                        ui.label('DENSITY').classes('text-[10px] text-slate-500 font-bold')
                        
                        def on_slider_change(e):
                            ui_settings['custom_cols'] = e.value
                            if refresh_gm_panel_callback: refresh_gm_panel_callback()
                            if refresh_custom_panel_callback: refresh_custom_panel_callback()

                        ui.slider(min=2, max=8, step=1, value=5, on_change=on_slider_change).props('dense color=primary label-always').classes('w-32').bind_value(ui_settings, 'custom_cols')

                    async def reload_gm():
                        c = "RuntimeGMClient.ReloadGM(true)"; 
                        success, msg = await mgr.send_to_port(state["sel_port"], c)
                        if success: ui.notify(msg)
                        else: ui.notify(msg, type='warning')
                    ui.button(icon='refresh', on_click=reload_gm).props('flat round dense color=grey')

                with ui.tab_panels(tabs, value='LuaGM').classes('w-full flex-1 bg-slate-50/50 p-4'):
                    with ui.tab_panel('LuaGM').classes('p-0 h-full flex flex-col'):
                        gm_area = ui.column().classes('w-full gap-4')
                        class GMExplorer:
                            def __init__(self):
                                self.root = []; self.path = []; self.search = ""; self.client_context = None
                            def load_context(self):
                                p = state["sel_port"]
                                gm_area.clear()
                                if p is None:
                                    with gm_area: ui.label("Select a Connected Port to view Game Commands").classes('w-full text-center text-slate-400 italic py-12')
                                    return
                                client = next((c for c in mgr.clients.values() if c.port == p), None)
                                self.client_context = client
                                if not client:
                                    with gm_area:
                                        with ui.column().classes('w-full items-center justify-center py-12 opacity-50 gap-2'):
                                            ui.icon('link_off', size='xl').classes('text-slate-300')
                                            ui.label("Waiting for Device Connection...").classes('text-slate-400 font-medium')
                                    return
                                self.root = client.gm_tree
                                if not self.root:
                                    with gm_area: ui.label("Waiting for GM List... (Try Refresh)").classes('w-full text-center text-slate-400 py-12')
                                else: self.render()
                            def nav(self, idx): self.path = [] if idx == -1 else self.path[:idx+1]; self.render()
                            def enter(self, node): self.path.append(node); self.render()
                            def render(self):
                                gm_area.clear()
                                with gm_area:
                                    with ui.row().classes('w-full items-center bg-white border border-slate-200 rounded px-3 py-2 shadow-sm gap-2'):
                                        ui.button(icon='home', on_click=lambda: self.nav(-1)).props('flat dense round size=sm color=grey')
                                        for i, n in enumerate(self.path):
                                            ui.icon('chevron_right', size='xs').classes('text-slate-300')
                                            ui.button(n['name'], on_click=lambda x=i: self.nav(x)).props('flat dense no-caps size=sm')
                                        ui.space()
                                        ui.input(placeholder='Search').props('dense borderless').classes('bg-slate-50 px-2 rounded w-40').bind_value(self, 'search').on('input', self.render)
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
                                            if typ == 'Toggle':
                                                async def tgl(e, i=nid):
                                                    success, msg = await mgr.send_gm_to_port(state["sel_port"], i, e.value)
                                                    if not success: ui.notify(msg, type='warning')
                                                initial_val = self.client_context.ui_states.get(nid, False)
                                                with ui.card().classes('p-2 h-20 justify-between items-center border border-slate-200'):
                                                    ui.label(name).classes('text-xs font-bold text-slate-700 text-center leading-tight'); ui.switch(value=initial_val, on_change=tgl).props('dense size=sm color=green')
                                            elif typ == 'Input':
                                                async def inp(e, i=nid):
                                                    success, msg = await mgr.send_gm_to_port(state["sel_port"], i, e.value)
                                                    if not success: ui.notify(msg, type='warning')
                                                initial_val = self.client_context.ui_states.get(nid, "")
                                                with ui.card().classes('p-2 h-20 justify-center gap-1 border border-slate-200'):
                                                    ui.label(name).classes('text-[10px] font-bold text-slate-500 truncate w-full'); ui.input(value=initial_val, on_change=inp).props('dense outlined input-style="font-size:12px"').classes('w-full')
                                            elif typ == 'Btn':
                                                async def clk(i=nid):
                                                    success, msg = await mgr.send_gm_to_port(state["sel_port"], i)
                                                    if success: ui.notify(msg)
                                                    else: ui.notify(msg, type='warning')
                                                with ui.button(on_click=clk).classes('bg-white border border-slate-200 hover:bg-blue-50 p-2 h-20 rounded text-left flex flex-col justify-between'):
                                                    ui.label(name).classes('text-xs font-bold text-slate-700 whitespace-normal leading-tight'); ui.icon('bolt', size='xs').classes('self-end text-amber-400')
                                            elif typ == 'SubBox':
                                                with ui.card().classes('cursor-pointer hover:shadow-md transition-all p-3 h-20 justify-center items-center gap-2 border border-slate-200').on('click', lambda x=n: self.enter(x)):
                                                    ui.icon('folder', size='sm').classes('text-blue-300'); ui.label(name).classes('text-xs font-bold text-center leading-tight')
                        explorer = GMExplorer()
                        
                        # --- FIX: Removed conditional optimization. Always load context on refresh. ---
                        def refresh_gm_proxy(): 
                            explorer.load_context()
                        
                        refresh_gm_panel_callback = refresh_gm_proxy
                        
                        def on_data_update(cid):
                            p = state["sel_port"]
                            if p:
                                c = next((c for c in mgr.clients.values() if c.port == p), None)
                                if c and c.id == cid: explorer.load_context()
                        mgr.on_client_data_update = on_data_update
                        explorer.load_context()

                    with ui.tab_panel('CustomGM').classes('p-0'):
                        with ui.row().classes('w-full mb-3 justify-between items-center'):
                            ui.button('Add', icon='add', on_click=lambda: add_dlg.open()).props('unelevated dense color=primary size=sm')
                            
                            with ui.dialog() as add_dlg, ui.card().classes('w-96 p-4 gap-3'):
                                ui.label('New Command').classes('font-bold')
                                n_in = ui.input('Name').classes('w-full')
                                c_in = ui.textarea('Code').classes('w-full bg-slate-50')
                                ui.button('Save', on_click=lambda: (custom_mgr.add(n_in.value, c_in.value), add_dlg.close(), r_cust())).props('unelevated color=primary')
                        
                        c_grid = ui.grid().classes('w-full gap-3')
                        
                        def r_cust():
                            c_grid.clear()
                            cols = ui_settings['custom_cols']
                            c_grid.style(f'grid-template-columns: repeat({cols}, minmax(0, 1fr))')
                            
                            with c_grid:
                                for idx, item in enumerate(custom_mgr.commands):
                                    with ui.card().classes('p-3 h-24 hover:shadow-md border border-slate-200 relative group justify-between'):
                                        async def run_c(c=item['cmd']):
                                            success, msg = await mgr.send_to_port(state["sel_port"], c)
                                            if success: ui.notify(msg)
                                            else: ui.notify(msg, type='warning')
                                        
                                        with ui.column().classes('w-full h-full cursor-pointer justify-between gap-1').on('click', run_c):
                                            ui.label(item['name']).classes('font-bold text-xs leading-tight break-all line-clamp-2')
                                            ui.label(item['cmd']).classes('text-[10px] mono text-slate-400 truncate w-full')
                                        
                                        ui.button(icon='close', on_click=lambda i=idx: (custom_mgr.delete(i), r_cust())).props('flat dense round size=xs color=red').classes('absolute top-1 right-1 opacity-0 group-hover:opacity-100')
                        
                        refresh_custom_panel_callback = r_cust
                        r_cust()

        # Logs
        with ui.column().classes('w-[280px] h-full bg-white border-l border-slate-200 flex-none flex flex-col'):
            with ui.row().classes('h-[40px] px-3 items-center justify-between border-b border-slate-100 bg-slate-50 w-full'):
                ui.label('LOGS').classes('text-[10px] font-bold text-slate-500')
                ui.button(icon='delete', on_click=lambda: log_container.clear()).props('flat dense round size=xs color=grey')
            log_container = ui.column().classes('w-full flex-1 overflow-y-auto gap-0')

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
    ui.run(title="GM Enterprise", host="0.0.0.0", port=9529, reload=False, favicon='🚀')