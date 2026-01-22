#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GM 控制台 v4.0 - UX Optimized
Fixes:
1. Persistent Toolbar (Search + Home + Reload)
2. Integrated Reload Button in Breadcrumbs
3. Better Grid Layout for Custom GM
"""

import asyncio
import json
import socket
import os
import sys
from datetime import datetime
from dataclasses import dataclass, field
from typing import Dict, Optional, Callable, List
from nicegui import ui, app

# ============================================================================
# Theme & Config
# ============================================================================
THEME = {
    "bg": "#0F172A",          # Slate 900
    "surface": "#1E293B",     # Slate 800
    "surface_hover": "#334155", # Slate 700
    "border": "#334155",      # Slate 700
    "text": "#F8FAFC",        # Slate 50
    "text_muted": "#94A3B8",  # Slate 400
    "primary": "#3B82F6",     # Blue 500
    "primary_hover": "#2563EB", # Blue 600
    "success": "#22C55E",     # Green 500
    "error": "#EF4444",       # Red 500
    "accent": "#F59E0B",      # Amber 500 (For Reload Btn)
}

ICONS = {
    "plus": '''<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 5v14"/><path d="M5 12h14"/></svg>''',
    "trash": '''<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/></svg>''',
    "play": '''<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>''',
    "x": '''<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>''',
    "pc": '''<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8"/><path d="M12 17v4"/></svg>''',
    "mobile": '''<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="5" y="2" width="14" height="20" rx="2"/><path d="M12 18h.01"/></svg>''',
    "wifi": '''<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 12.55a11 11 0 0 1 14.08 0"/><path d="M1.42 9a16 16 0 0 1 21.16 0"/><path d="M8.53 16.11a6 6 0 0 1 6.95 0"/><line x1="12" y1="20" x2="12.01" y2="20"/></svg>''',
    "wait": '''<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>''',
}

# ============================================================================
# Logic Components (No Changes to Server Logic)
# ============================================================================
class CustomGmManager:
    def __init__(self):
        self.file_path = os.path.join(os.path.dirname(__file__), "custom_gm.json")
        self.commands = self.load()

    def load(self):
        if not os.path.exists(self.file_path): return []
        try:
            with open(self.file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
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
    connect_time: datetime = field(default_factory=datetime.now)

@dataclass
class Log:
    time: datetime
    level: str
    msg: str

class ServerMgr:
    def __init__(self):
        self.listeners = {} # port -> server
        self.clients = {}   # cid -> Client
        self.logs = []
        self.cmd_id = 1000
        self.on_update = None
        self.on_log = None
        self.on_gm_list = None
    
    async def add_listener(self, port):
        if port in self.listeners: return
        print(f"[Mgr] Adding listener {port}")
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("0.0.0.0", port))
            sock.close()
        except: pass
        
        try:
            srv = await asyncio.start_server(lambda r,w: self._h(r,w,port), "0.0.0.0", port, reuse_address=True)
            self.listeners[port] = srv
            if self.on_update: self.on_update()
        except Exception as e:
            print(f"Error binding {port}: {e}")

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
                    try: 
                        c.writer.close()
                        await c.writer.wait_closed()
                    except: pass
            if self.on_update: self.on_update()

    async def _h(self, r, w, port):
        addr = w.get_extra_info("peername")
        cid = f"{addr[0]}:{addr[1]}"
        
        zombies = [old_cid for old_cid, c in self.clients.items() if c.port == port]
        for old_cid in zombies:
            old_c = self.clients.pop(old_cid)
            try: old_c.writer.close()
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
                except Exception as e:
                    print(f"[Mgr] JSON Error: {e}")
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
            if len(self.logs)>200: self.logs = self.logs[-200:]
            if self.on_log: self.on_log(l)
        elif t == "GM_LIST":
            data = pkt.get("data")
            if self.on_gm_list: self.on_gm_list(data)

    async def send(self, cid, cmd):
        c = self.clients.get(cid)
        if not c: return
        self.cmd_id += 1
        try:
            data = json.dumps({"type":"EXEC","id":self.cmd_id,"cmd":cmd}, ensure_ascii=False)+"\n"
            c.writer.write(data.encode())
            await c.writer.drain()
        except: pass

    async def send_gm(self, cid, gm_id, value=None):
        c = self.clients.get(cid)
        if not c: return
        self.cmd_id += 1
        try:
            data = json.dumps({"type":"EXEC_GM","id":gm_id,"value":value}, ensure_ascii=False)+"\n"
            c.writer.write(data.encode())
            await c.writer.drain()
        except: pass

    async def broadcast(self, cmd):
        for cid in list(self.clients.keys()): await self.send(cid, cmd)

mgr = ServerMgr()
state = {"sel": None}

# ============================================================================
# UI Components
# ============================================================================
@ui.page('/')
def main():
    ui.add_head_html(f'''
    <link href="https://fonts.googleapis.com/css2?family=Fira+Code:wght@400;500&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
    <style>
        :root {{ --bg: {THEME["bg"]}; --surface: {THEME["surface"]}; --border: {THEME["border"]}; --text: {THEME["text"]}; --primary: {THEME["primary"]}; }}
        body {{ background: var(--bg); color: var(--text); font-family: 'Inter', sans-serif; }}
        .mono {{ font-family: 'Fira Code', monospace; }}
        .card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 20px; }}
        .btn {{ display: inline-flex; align-items: center; justify-content: center; border-radius: 8px; cursor: pointer; transition: 0.2s; border: 1px solid transparent; font-size: 13px; font-weight: 500; gap: 8px; }}
        .btn:hover {{ filter: brightness(1.1); }}
        .btn-pri {{ background: var(--primary); color: white; padding: 8px 16px; }}
        .btn-sec {{ background: transparent; border-color: var(--border); color: var(--text); padding: 8px 16px; }}
        .btn-ghost {{ background: transparent; color: {THEME["text_muted"]}; padding: 4px; }}
        .btn-ghost:hover {{ color: var(--text); background: rgba(255,255,255,0.05); }}
        .input {{ background: rgba(0,0,0,0.2); border: 1px solid var(--border); color: var(--text); padding: 8px 12px; border-radius: 8px; outline: none; }}
        .input:focus {{ border-color: var(--primary); }}
        .listener-card {{ background: rgba(255,255,255,0.03); border: 1px solid var(--border); border-radius: 8px; padding: 12px; margin-bottom: 8px; transition: 0.2s; }}
        .listener-card.active {{ border-color: var(--primary); background: rgba(59,130,246,0.1); }}
        .status-badge {{ font-size: 11px; padding: 2px 6px; border-radius: 4px; font-weight: 600; text-transform: uppercase; }}
        .status-wait {{ background: rgba(255,255,255,0.1); color: {THEME["text_muted"]}; }}
        .status-conn {{ background: rgba(34,197,94,0.1); color: {THEME["success"]}; }}
        .log-line {{ font-size: 12px; padding: 2px 0; border-bottom: 1px solid rgba(255,255,255,0.03); }}
    </style>
    ''')

    left_container = None
    log_container = None
    target_label = None

    # --- Sidebar Refresh Logic ---
    def refresh_sidebar():
        if not left_container: return
        left_container.clear()
        with left_container:
            with ui.row().style('width: 100%; gap: 8px; margin-bottom: 20px'):
                ipt = ui.input(value='12581').classes('input mono').style('flex: 1')
                async def add():
                    try: await mgr.add_listener(int(ipt.value))
                    except: pass
                with ui.button(on_click=add).classes('btn btn-pri'):
                    ui.html(ICONS["plus"], sanitize=False)
                    ui.label('添加端口')

            ui.label('当前连接').style(f'color: {THEME["text_muted"]}; font-size: 11px; font-weight: 700; margin-bottom: 10px')

            sorted_ports = sorted(mgr.listeners.keys())
            if not sorted_ports:
                ui.label('无监听端口').style(f'color: {THEME["text_muted"]}; font-size: 13px; font-style: italic')
            
            for port in sorted_ports:
                connected_client = None
                for c in mgr.clients.values():
                    if c.port == port:
                        connected_client = c
                        break
                is_sel = state["sel"] == (connected_client.id if connected_client else None)
                
                with ui.column().classes(f'listener-card {"active" if is_sel else ""}').style('width: 100%; gap: 8px'):
                    with ui.row().style('width: 100%; justify-content: space-between; align-items: center'):
                        with ui.row().style('align-items: center; gap: 8px'):
                            ui.html(ICONS["wifi"], sanitize=False).style(f'color: {THEME["primary"]}')
                            ui.label(f':{port}').classes('mono').style('font-weight: 600')
                        async def rm(p=port): await mgr.remove_listener(p)
                        with ui.button(on_click=rm).classes('btn-ghost'):
                            ui.html(ICONS["x"], sanitize=False)
                    
                    if connected_client:
                        def sel(cid=connected_client.id, dev=connected_client.device):
                            state["sel"] = cid
                            if target_label: target_label.set_text(f'目标: {dev}')
                            refresh_sidebar()
                        with ui.row().style('width: 100%; align-items: center; gap: 10px; cursor: pointer').on('click', sel):
                            ui.html(ICONS["pc"] if "windows" in connected_client.platform.lower() else ICONS["mobile"], sanitize=False).style('color: #fff')
                            with ui.column().style('gap: 0; flex: 1'):
                                ui.label(connected_client.device).style('font-size: 13px; font-weight: 500')
                                ui.label(connected_client.platform).style(f'font-size: 11px; color: {THEME["text_muted"]}')
                            ui.label('已连接').classes('status-badge status-conn')
                    else:
                        with ui.row().style('width: 100%; align-items: center; gap: 10px; opacity: 0.6'):
                            ui.html(ICONS["wait"], sanitize=False)
                            ui.label('等待设备接入...').style('font-size: 13px; font-style: italic')

    def msg(l):
        if not log_container: return
        with log_container:
            color = THEME["text"] if l.level == 'info' else THEME["error"]
            ui.label(f'[{l.time.strftime("%H:%M:%S")}] {l.msg}').style(f'color: {color}').classes('log-line mono')

    mgr.on_update = refresh_sidebar
    mgr.on_log = msg

    # ==========================================================================
    # Main Layout
    # ==========================================================================
    with ui.row().style('height: 100vh; width: 100%; gap: 0'):
        # Sidebar
        with ui.column().classes('card').style('width: 300px; height: 100%; border-radius: 0; border: none; border-right: 1px solid var(--border)'):
            ui.label('GM 控制台 v4.0').style('font-size: 18px; font-weight: 700; margin-bottom: 24px')
            left_container = ui.column().style('width: 100%; gap: 0')
            refresh_sidebar()
        
        # Right Content
        with ui.column().style('flex: 1; height: 100%; padding: 24px; gap: 20px'):
            
            # 1. Editor Area (Top) - Cleaned up
            with ui.expansion('Lua 执行', icon='code').classes('w-full border border-slate-700 rounded mb-4 bg-slate-800/30').props('dense'):
                 with ui.column().classes('p-4 w-full gap-4'):
                    with ui.row().style('width: 100%; justify-content: space-between'):
                        ui.label('执行区域').style(f'color: {THEME["text_muted"]}; font-size: 11px; font-weight: 700')
                        target_label = ui.label('目标: 所有设备').style(f'color: {THEME["text_muted"]}; font-size: 11px')
                    
                    txt = ui.textarea(placeholder='-- 输入 Lua 代码...').classes('mono').style('width: 100%; background: rgba(0,0,0,0.2); border: none; outline: none; color: #fff; padding: 12px; border-radius: 8px; resize: none')
                    
                    with ui.row().style('gap: 10px'):
                        async def run():
                            if not txt.value: return
                            if state["sel"]: await mgr.send(state["sel"], txt.value)
                            else: await mgr.broadcast(txt.value)
                        with ui.button(on_click=run).classes('btn btn-pri'):
                            ui.html(ICONS["play"], sanitize=False)
                            ui.label('运行代码')
                        with ui.button(on_click=lambda: txt.set_value('')).classes('btn btn-sec'):
                            ui.html(ICONS["trash"], sanitize=False)
                            ui.label('清空')
            
            # 2. Main GM Panel
            gm_container = ui.column().classes('card').style('flex: 2; width: 100%; overflow-y: auto; gap: 0; padding: 0')
            with gm_container:
                with ui.tabs().classes('w-full').style('border-bottom: 1px solid var(--border)') as main_tabs:
                    tab_lua = ui.tab('LuaGM', label='LuaGM').style(f'color: {THEME["text"]}')
                    tab_custom = ui.tab('CustomGM', label='自定义 GM').style(f'color: {THEME["text"]}')

                with ui.tab_panels(main_tabs, value='LuaGM').classes('w-full bg-transparent').style('padding: 0; flex: 1'):
                    
                    # --- LuaGM Panel ---
                    with ui.tab_panel('LuaGM').style('padding: 16px; gap: 10px; display: flex; flex-direction: column'):
                         
                         lua_gm_root_div = ui.column().classes('w-full gap-4')

                         class GMExplorer:
                             def __init__(self, root_structure):
                                 self.root = root_structure
                                 self.current_path = [] 
                                 self.search_term = ""
                                 self.view_container = None
                                 self.breadcrumbs_container = None
                                 
                                 # Setup skeleton immediately (Toolbar)
                                 self.setup_skeleton()
                                 # Render initial content
                                 self.render_content()

                             def update_tree(self, new_structure):
                                 self.root = new_structure
                                 self.render_content()

                             def get_view_nodes(self):
                                 if self.search_term:
                                     results = []
                                     def _search(nodes):
                                         for n in nodes:
                                             if self.search_term.lower() in n.get("name", "").lower():
                                                 results.append(n)
                                             if n.get("children"): _search(n["children"])
                                     _search(self.root)
                                     return results
                                 
                                 if not self.current_path: return self.root
                                 return self.current_path[-1].get("children", [])

                             def enter_folder(self, node):
                                 self.current_path.append(node)
                                 self.render_content()

                             def go_to_level(self, index):
                                 if index == -1: self.current_path = []
                                 else: self.current_path = self.current_path[:index+1]
                                 self.render_content()

                             def setup_skeleton(self):
                                 with lua_gm_root_div:
                                     # Top Toolbar
                                     with ui.row().classes('w-full items-center gap-4 bg-slate-800/50 p-3 rounded-lg border border-slate-700'):
                                         # Search
                                         def on_search(e):
                                             self.search_term = e.value
                                             self.render_content()
                                         ui.input(placeholder='🔍 搜索...', on_change=on_search).classes(
                                             'bg-slate-900 border border-slate-700 rounded px-2 py-1 text-xs w-48 focus:border-blue-500 transition-colors'
                                         ).props('dense outlined clearable input-class="text-white"').style('min-height: 32px')

                                         ui.separator().props('vertical spaced')

                                         # Breadcrumbs (Home + Reload)
                                         self.breadcrumbs_container = ui.row().classes('items-center gap-1 flex-1 overflow-x-auto no-wrap')
                                     
                                     # Content Grid
                                     self.view_container = ui.column().classes('w-full gap-4')

                             def render_content(self):
                                 # 1. Update Breadcrumbs
                                 if self.breadcrumbs_container:
                                     self.breadcrumbs_container.clear()
                                     with self.breadcrumbs_container:
                                         # Home
                                         ui.button(on_click=lambda: self.go_to_level(-1)).props('icon=home flat round dense size=sm').classes(
                                             f'text-slate-400 hover:text-white {"text-blue-400" if not self.current_path else ""}'
                                         )
                                         
                                         # [NEW] Force Reload Button next to Home
                                         async def force_reload():
                                             if state["sel"]: await mgr.send(state["sel"], "RuntimeGMClient.ReloadGM(true)")
                                             else: await mgr.broadcast("RuntimeGMClient.ReloadGM(true)")
                                             ui.notify('已发送重载指令', type='info', color=THEME['accent'])
                                         
                                         ui.button(on_click=force_reload).classes('text-amber-500 hover:text-amber-300').props('icon=refresh flat round dense').tooltip('强制重载 GM 配置')

                                         # Separator & Path
                                         if self.current_path:
                                              ui.icon('chevron_right', size='xs').classes('text-slate-600')
                                         
                                         for i, node in enumerate(self.current_path):
                                             is_last = i == len(self.current_path) - 1
                                             if not is_last:
                                                 ui.button(node['name'], on_click=lambda idx=i: self.go_to_level(idx)).classes(
                                                     'text-slate-400 hover:text-white text-xs font-bold'
                                                 ).props('flat dense no-caps')
                                                 ui.icon('chevron_right', size='xs').classes('text-slate-600')
                                             else:
                                                 ui.label(node['name']).classes('text-white font-bold text-xs bg-slate-700 px-2 py-1 rounded')

                                 # 2. Update Grid
                                 if self.view_container:
                                     self.view_container.clear()
                                     with self.view_container:
                                         nodes = self.get_view_nodes()
                                         
                                         if not nodes:
                                             with ui.column().classes('w-full items-center justify-center py-12 opacity-50'):
                                                 ui.icon('folder_off', size='4xl').classes('text-slate-600 mb-2')
                                                 ui.label('暂无 GM 指令' if not self.root else '没有找到相关指令').classes('text-slate-500 text-sm')
                                                 if not self.root:
                                                     ui.label('请连接游戏并等待数据推送...').classes('text-xs text-blue-400 mt-2')
                                         else:
                                             # Using Responsive Grid
                                             with ui.grid().classes('w-full grid-cols-2 md:grid-cols-4 lg:grid-cols-5 gap-3'):
                                                 for node in nodes:
                                                     ntype = node.get("type", "Unknown")
                                                     name = node.get("name", "Unknown")
                                                     nid = node.get("id")

                                                     if ntype == "SubBox":
                                                         with ui.card().classes(
                                                             'bg-slate-800 hover:bg-slate-700 border-l-4 border-l-amber-500 border-y border-r border-slate-700 cursor-pointer transition-all hover:-translate-y-1 active:scale-95 group relative p-0'
                                                         ).style('min-height: 80px').on('click', lambda n=node: self.enter_folder(n)):
                                                              with ui.row().classes('w-full h-full items-center p-3 gap-3 no-wrap'):
                                                                  ui.icon('folder', size='md').classes('text-amber-500 group-hover:text-amber-300 transition-colors')
                                                                  with ui.column().classes('gap-0 overflow-hidden'):
                                                                      ui.label(name).classes('text-gray-200 font-bold text-sm leading-tight group-hover:text-white truncate w-full')
                                                                      count = len(node.get("children", []))
                                                                      ui.label(f'{count} Items').classes('text-[10px] text-slate-500 uppercase tracking-wider')

                                                     elif ntype == "Btn":
                                                         async def on_click_btn(i=nid):
                                                             if state["sel"]: await mgr.send_gm(state["sel"], i)
                                                             else: ui.notify('请先选择一个设备', type='warning')
                                                         with ui.button(on_click=on_click_btn).classes(
                                                             'h-full min-h-[60px] bg-slate-700/50 hover:bg-blue-600 border border-slate-600 hover:border-blue-400 transition-all text-left px-3 py-2 rounded relative group active:scale-95 flex items-start'
                                                         ):
                                                             with ui.column().classes('gap-1 w-full'):
                                                                 ui.label(name).classes('text-xs font-medium text-gray-300 group-hover:text-white break-words whitespace-normal leading-snug w-full')

                                                     elif ntype == "Toggle":
                                                         kid_id = node.get("id")
                                                         async def on_toggle(e, i=kid_id):
                                                             if state["sel"]: await mgr.send_gm(state["sel"], i, e.value)
                                                         with ui.card().classes('bg-slate-800 p-2 border border-slate-700 flex flex-row items-center justify-between'):
                                                             ui.label(name).classes('text-xs text-gray-400')
                                                             ui.switch(on_change=on_toggle).props('dense color=blue size=sm')

                                                     elif ntype == "Input":
                                                         kid_id = node.get("id")
                                                         async def on_input(e, i=kid_id):
                                                             if state["sel"]: await mgr.send_gm(state["sel"], i, e.value)
                                                         with ui.card().classes('bg-slate-800 p-2 border border-slate-700 gap-1'):
                                                             ui.label(name).classes('text-[10px] text-gray-500 uppercase')
                                                             ui.input(on_change=on_input).classes('w-full text-xs p-0').props('dense borderless input-class="text-blue-400"')
                                                     
                                                     elif ntype == "Text":
                                                         with ui.card().classes('bg-slate-900/30 p-2 border border-dashed border-slate-700 items-center justify-center'):
                                                             ui.label(name).classes('text-xs text-gray-500 italic text-center')

                         # Init Explorer IMMEDIATELY with empty data
                         # This ensures toolbar is visible on startup
                         explorer = GMExplorer([])

                         def render_gm_panel(structure):
                             # Only update data, don't rebuild UI
                             explorer.update_tree(structure)
                         
                         mgr.on_gm_list = render_gm_panel
                    
                    # --- Custom GM Panel ---
                    with ui.tab_panel('CustomGM').style('padding: 16px; gap: 10px; display: flex; flex-direction: column'):
                        
                        # Add Button
                        with ui.dialog() as add_dialog, ui.card().style('width: 400px; gap: 12px; border: 1px solid var(--border)'):
                             ui.label('新增自定义 GM').style('font-size: 16px; font-weight: 700')
                             name_input = ui.input('按钮名称').classes('w-full')
                             cmd_input = ui.textarea('Lua 代码').classes('w-full mono').style('height: 100px')
                             
                             def do_add():
                                 if not name_input.value or not cmd_input.value: return
                                 custom_mgr.add(name_input.value, cmd_input.value)
                                 add_dialog.close()
                                 render_custom_list()
                             
                             with ui.row().classes('w-full justify-end'):
                                 ui.button('取消', on_click=add_dialog.close).classes('btn-ghost')
                                 ui.button('保存', on_click=do_add).classes('btn-primary')

                        ui.button('+ 新增自定义 GM', on_click=add_dialog.open).classes('btn btn-sec').style('width: 100%; border-style: dashed')
                        
                        # Custom List - Optimized Grid
                        # grid-cols-2 for mobile, 4 for tablet, 5 for PC
                        custom_list = ui.grid().classes('w-full grid-cols-2 md:grid-cols-4 lg:grid-cols-5 gap-3')
                        
                        def render_custom_list():
                            custom_list.clear()
                            with custom_list:
                                for idx, item in enumerate(custom_mgr.commands):
                                    # Fixed height card to prevent stretching
                                    with ui.card().classes('bg-slate-800 border border-slate-700 p-3 gap-2 relative group hover:border-blue-500 transition-colors h-24 justify-between'):
                                        # Run Logic
                                        async def run_c(c=item["cmd"]):
                                            if state["sel"]: await mgr.send(state["sel"], c)
                                            else: await mgr.broadcast(c)
                                        
                                        # Content (Clickable)
                                        with ui.column().classes('gap-1 w-full cursor-pointer h-full').on('click', run_c):
                                            ui.label(item["name"]).classes('text-sm font-bold text-gray-200 leading-tight mb-1 truncate w-11/12')
                                            ui.label(item["cmd"]).classes('text-xs text-slate-500 mono break-all line-clamp-2')
                                        
                                        # Delete Button (Top Right)
                                        def delete_c(i=idx):
                                            custom_mgr.delete(i)
                                            render_custom_list()
                                        
                                        ui.button(on_click=delete_c).classes('absolute top-1 right-1 text-slate-600 hover:text-red-400').props(f'icon={ICONS["trash"]} flat dense round size=xs')

                        render_custom_list()

        # Logs
        with ui.column().classes('card').style('width: 320px; height: 100%; border-radius: 0; border: none; border-left: 1px solid var(--border)'):
            with ui.row().style('width: 100%; justify-content: space-between; margin-bottom: 12px'):
                ui.label('日志').style(f'color: {THEME["text_muted"]}; font-size: 11px; font-weight: 700')
                ui.button(on_click=lambda: log_container.clear()).classes('btn-ghost').props(f'innerHTML="{ICONS["trash"]}"')
            log_container = ui.column().classes('mono').style('width: 100%; flex: 1; overflow-y: auto; gap: 0')

async def startup(): await mgr.add_listener(12581)
app.on_startup(startup)

def kill_port_process(port):
    import subprocess
    try:
        cmd = f'netstat -ano | findstr :{port}'
        try: result = subprocess.check_output(cmd, shell=True).decode()
        except: return
        pids = set()
        for line in result.splitlines():
            if f':{port}' in line and 'LISTENING' in line:
                parts = line.split()
                if len(parts) >= 5: pids.add(parts[-1])
        for pid in pids:
            if pid == str(os.getpid()): continue
            os.system(f'taskkill /F /PID {pid} >nul 2>&1')
    except: pass

if __name__ in {"__main__", "__mp_main__"}:
    kill_port_process(9529)
    ui.run(title="GM Console", host="0.0.0.0", port=9529, dark=True, reload=False)