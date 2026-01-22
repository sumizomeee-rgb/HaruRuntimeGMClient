#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GM 控制台 v5.1 - High Contrast UI
UI Fixes: Improved Sidebar Contrast & Text Readability
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

# Windows asyncio 异常处理器 - 抑制非致命的 ConnectionResetError
def _windows_exception_handler(loop, context):
    """处理 Windows 上 asyncio 的 ConnectionResetError 警告"""
    exception = context.get('exception')
    # 仅抑制 ConnectionResetError (WinError 10054)
    if isinstance(exception, ConnectionResetError):
        return  # 静默忽略
    # 其他异常使用默认处理
    loop.default_exception_handler(context)

# Windows 异常处理器将在 startup 回调中设置（避免 DeprecationWarning）

# ============================================================================
# Logic Components (核心逻辑保持不变)
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
        self.listeners = {} 
        self.clients = {}   
        self.logs = []
        self.cmd_id = 1000
        self.on_update = None
        self.on_log = None
        self.on_gm_list = None
    
    async def add_listener(self, port):
        if port in self.listeners: return
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
        # --- 新增这一行 ---
        print(f"[Debug] Python Server received connection on port {port}") 
        # ----------------
        
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
        
        print(f"[Process] Received packet type: {t}")  # 调试日志
        
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
            print(f"[Process] GM_LIST received with {len(data) if data else 0} items")  # 调试日志
            if self.on_gm_list: 
                try:
                    self.on_gm_list(data)
                    print("[Process] on_gm_list callback executed")
                except Exception as e:
                    print(f"[Process] on_gm_list error: {e}")


    async def send(self, cid, cmd):
        c = self.clients.get(cid)
        if not c: 
            print(f"[Send] Client {cid} not found!")
            return
        self.cmd_id += 1
        try:
            data = json.dumps({"type":"EXEC","id":self.cmd_id,"cmd":cmd}, ensure_ascii=False)+"\n"
            c.writer.write(data.encode())
            await c.writer.drain()
            print(f"[Send] Sent to {cid}: {cmd[:50]}...")
        except Exception as e: 
            print(f"[Send] Error: {e}")

    async def send_gm(self, cid, gm_id, value=None):
        c = self.clients.get(cid)
        if not c: return
        self.cmd_id += 1
        try:
            data = json.dumps({"type":"EXEC_GM","id":gm_id,"value":value}, ensure_ascii=False)+"\n"
            c.writer.write(data.encode())
            await c.writer.drain()
            print(f"[SendGM] Sent GM {gm_id} to {cid}")
        except Exception as e: 
            print(f"[SendGM] Error: {e}")

    async def broadcast(self, cmd):
        print(f"[Broadcast] Broadcasting to {len(self.clients)} clients: {cmd[:50]}...")
        for cid in list(self.clients.keys()): await self.send(cid, cmd)

    async def broadcast_gm(self, gm_id, value=None):
        print(f"[BroadcastGM] Broadcasting GM {gm_id} to {len(self.clients)} clients...")
        for cid in list(self.clients.keys()): await self.send_gm(cid, gm_id, value)

mgr = ServerMgr()
state = {"sel": None}

# ============================================================================
# UI Components
# ============================================================================
@ui.page('/')
def main():
    ui.add_head_html('''
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
    <style>
        body {
            font-family: 'Inter', sans-serif;
            background-color: #F8FAFC; 
            color: #334155;
        }
        .mono { font-family: 'JetBrains Mono', monospace; }
        
        ::-webkit-scrollbar { width: 6px; height: 6px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: #CBD5E1; border-radius: 3px; }
        ::-webkit-scrollbar-thumb:hover { background: #94A3B8; }

        .ent-card {
            background: white;
            border: 1px solid #E2E8F0;
            border-radius: 8px;
            box-shadow: 0 1px 2px 0 rgba(0, 0, 0, 0.05);
        }
        
        /* 强制覆盖 input 的 placeholder 颜色 */
        .dark-input input::placeholder {
            color: #64748B !important; 
            opacity: 1;
        }
        .dark-input input {
            color: white !important;
        }
    </style>
    ''')

    left_container = None
    log_container = None
    target_label = None

    # --- Sidebar Refresh Logic (优化对比度) ---
    def refresh_sidebar():
        try:
            if not left_container: return
            left_container.clear()
            with left_container:
                # Add Port Input - 使用更亮的背景色让输入框更明显
                with ui.row().classes('w-full gap-2 mb-4'):
                    # 增加 dark-input class 并调整背景色为 Slate-800
                    ipt = ui.input(placeholder='Port').props('dense outlined input-class="text-white"').classes('flex-1 dark-input bg-slate-800/50 rounded').style('font-size: 13px; --q-primary: #94A3B8')
                    ipt.value = '12581'
                    
                    async def add():
                        try: await mgr.add_listener(int(ipt.value))
                        except: pass
                    
                    ui.button(on_click=add, icon='add').props('dense flat color=white').classes('min-w-[32px] px-0 hover:bg-slate-800 rounded')
                
                # Listeners List
                sorted_ports = sorted(mgr.listeners.keys())
                
                if not sorted_ports:
                    ui.label('No listeners active').classes('text-slate-500 text-xs italic text-center w-full py-4')
                
                for port in sorted_ports:
                    connected_client = next((c for c in mgr.clients.values() if c.port == port), None)
                    is_sel = state["sel"] == (connected_client.id if connected_client else None)
                    
                    # --- 动态配色逻辑 ---
                    if is_sel:
                        # 选中状态：高亮蓝背景，文字强制为白色/极浅蓝
                        card_bg = 'bg-blue-600 shadow-lg scale-[1.02]'
                        border_col = 'border-blue-500'
                        text_primary = 'text-white'
                        text_secondary = 'text-blue-100' # 浅蓝白色，对比度高
                        icon_color = 'text-blue-200'
                        hover_effect = ''
                    else:
                        # 未选中状态：深灰背景，文字为亮灰
                        card_bg = 'bg-slate-800/60'
                        border_col = 'border-slate-700'
                        text_primary = 'text-slate-200' # 亮灰
                        text_secondary = 'text-slate-400' # 中灰
                        icon_color = 'text-slate-500'
                        hover_effect = 'hover:border-slate-500 hover:bg-slate-800 transition-all'
                    
                    with ui.column().classes(f'w-full {card_bg} border {border_col} rounded p-3 mb-2 cursor-pointer relative group {hover_effect}'):
                        # Port Header
                        with ui.row().classes('w-full justify-between items-center mb-1'):
                            with ui.row().classes('items-center gap-2'):
                                ui.icon('lan', size='xs').classes(icon_color)
                                ui.label(f':{port}').classes(f'{text_primary} font-mono text-sm font-bold')
                            
                            async def rm(p=port): await mgr.remove_listener(p)
                            ui.button(on_click=rm).props('icon=close flat dense size=sm color=grey').classes('opacity-0 group-hover:opacity-100 transition-opacity')

                        # Connection State
                        if connected_client:
                            def sel(cid=connected_client.id):
                                state["sel"] = cid
                                if target_label: 
                                    target_label.set_text(f'Target: {connected_client.device}')
                                    target_label.classes('text-blue-600 font-bold')
                                refresh_sidebar()
                            
                            # 安全地注册点击事件，避免在非 UI 上下文中调用时崩溃
                            try:
                                ui.context.get_client().layout.children[-1].on('click', sel)
                            except Exception:
                                pass  # 从 TCP handler 调用时没有 UI 上下文，静默忽略
                            
                            ui.separator().classes(f'my-1 {"bg-blue-500" if is_sel else "bg-slate-700"}')
                            with ui.row().classes('items-center gap-2 w-full'):
                                ui.icon('smartphone', size='xs').classes('text-green-400')
                                with ui.column().classes('gap-0 flex-1 min-w-0'):
                                    ui.label(connected_client.device).classes(f'{text_primary} text-xs font-bold truncate w-full')
                                    ui.label(connected_client.platform).classes(f'{text_secondary} text-[10px]')
                                
                                # Status Dot
                                ui.html('<div class="w-2 h-2 rounded-full bg-green-400 shadow-[0_0_8px_rgba(74,222,128,0.6)]"></div>')
                        else:
                            # Waiting 状态下的文字颜色也要跟随背景变
                            ui.label('Waiting for connection...').classes(f'{text_secondary} text-[11px] italic mt-1')
        except Exception:
            pass  # 从 TCP handler 调用时没有 UI 上下文，静默忽略

    def msg(l):
        try:
            if not log_container: return
            with log_container:
                text_col = 'text-slate-600'
                bg_col = 'hover:bg-slate-50'
                if l.level != 'info': 
                    text_col = 'text-red-600'
                    bg_col = 'bg-red-50 hover:bg-red-100'
                    
                with ui.row().classes(f'w-full gap-2 py-1 px-2 border-b border-slate-100 {bg_col} items-start'):
                    ui.label(l.time.strftime("%H:%M:%S")).classes('text-slate-400 text-[11px] mono mt-0.5 min-w-[50px]')
                    ui.label(l.msg).classes(f'{text_col} text-xs mono break-all leading-tight flex-1')
        except Exception:
            pass  # 从 TCP handler 调用时没有 UI 上下文，静默忽略

    mgr.on_update = refresh_sidebar
    mgr.on_log = msg

    # ==========================================================================
    # Layout Structure
    # ==========================================================================
    
    # Header
    with ui.header().classes('h-[50px] bg-white border-b border-slate-200 px-4 flex items-center justify-between z-20 shadow-sm'):
        with ui.row().classes('items-center gap-3'):
            ui.icon('terminal', size='sm').classes('text-slate-800')
            with ui.column().classes('gap-0'):
                ui.label('GM Console').classes('text-slate-800 font-bold text-sm leading-none')
                ui.label('Enterprise Edition').classes('text-slate-500 text-[10px] font-medium tracking-wide')
        
        with ui.row().classes('items-center gap-4'):
            with ui.row().classes('bg-slate-100 rounded-full px-3 py-1 items-center gap-3 border border-slate-200'):
                with ui.row().classes('items-center gap-1'):
                    ui.label('Clients').classes('text-[10px] text-slate-500 uppercase font-bold')
                    ui.label().bind_text_from(mgr.clients, lambda c: str(len(c))).classes('text-xs font-bold text-slate-700')
                ui.separator().props('vertical').classes('h-3 bg-slate-300')
                with ui.row().classes('items-center gap-1'):
                    ui.label('Status').classes('text-[10px] text-slate-500 uppercase font-bold')
                    ui.icon('check_circle', size='xs').classes('text-green-500')

    # Main Layout
    with ui.row().classes('w-full h-[calc(100vh-50px)] gap-0 no-wrap'):
        
        # 1. Sidebar (Dark Mode) - Updated BG color to Slate-900
        with ui.column().classes('w-[260px] h-full bg-[#0F172A] p-4 flex-none overflow-y-auto border-r border-slate-800 shadow-inner'):
            ui.label('CONNECTIONS').classes('text-slate-500 text-[10px] font-bold tracking-wider mb-3')
            left_container = ui.column().classes('w-full gap-0')
            refresh_sidebar()

        # 2. Main Content
        with ui.column().classes('flex-1 h-full bg-[#F8FAFC] p-6 overflow-y-auto gap-6'):
            
            # Lua Execution
            with ui.column().classes('ent-card w-full p-0 overflow-hidden'):
                with ui.row().classes('w-full bg-slate-50 border-b border-slate-200 px-4 py-2 justify-between items-center'):
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('code', size='xs').classes('text-slate-400')
                        ui.label('Lua Script Execution').classes('text-xs font-bold text-slate-700 uppercase')
                    target_label = ui.label('Target: Broadcast (All)').classes('text-xs text-slate-400 font-medium')

                with ui.column().classes('w-full p-4 gap-3 bg-white'):
                    txt = ui.textarea(placeholder='-- Enter Lua code here...').classes('w-full').props('borderless input-class="mono text-sm text-slate-700"').style('min-height: 120px; background-color: #FAFAFA; border: 1px solid #E2E8F0; border-radius: 6px; padding: 12px;')
                    
                    with ui.row().classes('w-full justify-between items-center'):
                        ui.label('Shift + Enter to insert new line').classes('text-[10px] text-slate-400')
                        with ui.row().classes('gap-2'):
                            ui.button('Clear', on_click=lambda: txt.set_value(''), icon='delete').props('flat dense color=grey size=sm').classes('px-3')
                            async def run():
                                if not txt.value: return
                                if state["sel"]: await mgr.send(state["sel"], txt.value)
                                else: await mgr.broadcast(txt.value)
                                ui.notify('Script executed', type='positive', position='top')
                            ui.button('Execute Script', on_click=run, icon='play_arrow').props('unelevated dense color=primary text-color=white size=sm').classes('px-4 rounded shadow-sm')

            # GM Command Center
            with ui.column().classes('ent-card w-full flex-1 min-h-[400px] overflow-hidden'):
                with ui.tabs().classes('w-full text-slate-600 bg-white border-b border-slate-200').props('align="left" dense active-color="primary" indicator-color="primary" narrow-indicator') as main_tabs:
                    ui.tab('LuaGM', label='Lua GM').classes('px-6 h-[48px] font-medium')
                    ui.tab('CustomGM', label='Custom GM').classes('px-6 h-[48px] font-medium')
                    ui.space()
                    with ui.row().classes('px-4 items-center'):
                         async def force_reload():
                             cmd = "RuntimeGMClient.ReloadGM(true)"
                             if state["sel"]: await mgr.send(state["sel"], cmd)
                             else: await mgr.broadcast(cmd)
                             ui.notify('Reload Command Sent', color='indigo')
                         ui.button(on_click=force_reload).props('icon=refresh flat round dense color=grey').tooltip('Force Reload GM')

                with ui.tab_panels(main_tabs, value='LuaGM').classes('w-full flex-1 bg-slate-50/50 p-4'):
                    
                    with ui.tab_panel('LuaGM').classes('p-0 h-full flex flex-col gap-4'):
                        gm_root_area = ui.column().classes('w-full gap-4')
                        
                        class GMExplorer:
                             def __init__(self, root_structure):
                                 self.root = root_structure
                                 self.current_path = [] 
                                 self.search_term = ""
                                 self.render()

                             def update_tree(self, new_structure):
                                 self.root = new_structure
                                 self.render()

                             def get_view_nodes(self):
                                 if self.search_term:
                                     results = []
                                     def _search(nodes):
                                         for n in nodes:
                                             if self.search_term.lower() in n.get("name", "").lower(): results.append(n)
                                             if n.get("children"): _search(n["children"])
                                     _search(self.root)
                                     return results
                                 return self.current_path[-1].get("children", []) if self.current_path else self.root

                             def enter(self, node):
                                 self.current_path.append(node)
                                 self.render()

                             def nav_to(self, index):
                                 self.current_path = [] if index == -1 else self.current_path[:index+1]
                                 self.render()

                             def render(self):
                                 gm_root_area.clear()
                                 with gm_root_area:
                                     with ui.row().classes('w-full items-center bg-white border border-slate-200 rounded px-3 py-2 shadow-sm gap-2'):
                                         ui.button(icon='home', on_click=lambda: self.nav_to(-1)).props('flat dense round size=sm color=grey')
                                         if self.current_path: ui.icon('chevron_right', size='xs').classes('text-slate-300')
                                         
                                         for i, node in enumerate(self.current_path):
                                             is_last = i == len(self.current_path) - 1
                                             if not is_last:
                                                 ui.button(node['name'], on_click=lambda idx=i: self.nav_to(idx)).props('flat dense no-caps size=sm').classes('text-slate-600 font-medium')
                                                 ui.icon('chevron_right', size='xs').classes('text-slate-300')
                                             else:
                                                 ui.label(node['name']).classes('bg-blue-50 text-blue-700 px-2 py-0.5 rounded text-xs font-bold border border-blue-100')
                                         
                                         ui.space()
                                         ui.input(placeholder='Search command...').props('dense borderless input-class="text-sm"').classes('bg-slate-50 px-2 rounded w-48 border border-transparent focus-within:border-blue-300 transition-colors').bind_value(self, 'search_term').on('input', self.render)

                                     nodes = self.get_view_nodes()
                                     if not nodes:
                                         with ui.column().classes('w-full py-12 items-center opacity-50'):
                                             ui.icon('inbox', size='xl').classes('text-slate-300')
                                             ui.label('No commands found' if self.root else 'Waiting for game data...').classes('text-slate-400 text-sm')
                                     
                                     with ui.grid().classes('w-full grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-3'):
                                         for node in nodes:
                                             ntype = node.get("type", "Unknown")
                                             name = node.get("name", "Unknown")
                                             nid = node.get("id")

                                             if ntype == "SubBox":
                                                 with ui.card().classes('bg-white border border-slate-200 hover:border-blue-400 hover:shadow-md transition-all cursor-pointer p-0 h-[80px] overflow-hidden group').on('click', lambda n=node: self.enter(n)):
                                                      with ui.row().classes('w-full h-full items-center p-3 gap-3 no-wrap'):
                                                          ui.icon('folder', size='md').classes('text-blue-200 group-hover:text-blue-500 transition-colors')
                                                          with ui.column().classes('gap-0 flex-1 min-w-0'):
                                                              ui.label(name).classes('text-sm font-semibold text-slate-700 truncate w-full group-hover:text-blue-700')
                                                              ui.label(f'{len(node.get("children", []))} items').classes('text-[10px] text-slate-400')

                                             elif ntype == "Btn":
                                                  async def clk(i=nid):
                                                      if state["sel"]: await mgr.send_gm(state["sel"], i)
                                                      else: await mgr.broadcast_gm(i)  # 未选设备时广播
                                                  
                                                  with ui.button(on_click=clk).classes('bg-white border border-slate-200 hover:border-blue-400 hover:bg-blue-50 transition-all p-3 h-[80px] rounded shadow-sm text-left flex items-start group relative'):
                                                      with ui.column().classes('gap-1 w-full'):
                                                          ui.icon('bolt', size='xs').classes('text-amber-400 absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity')
                                                          ui.label(name).classes('text-xs font-bold text-slate-700 group-hover:text-blue-700 leading-snug whitespace-normal break-words')

                                             elif ntype == "Toggle":
                                                 async def tgl(e, i=nid):
                                                     if state["sel"]: await mgr.send_gm(state["sel"], i, e.value)
                                                     else: await mgr.broadcast_gm(i, e.value) # Fallback to broadcast
                                                 with ui.card().classes('bg-white border border-slate-200 p-3 h-[80px] justify-between items-center shadow-sm'):
                                                     ui.label(name).classes('text-xs font-bold text-slate-700 leading-tight')
                                                     ui.switch(on_change=tgl).props('dense size=sm color=green')
                                             
                                             elif ntype == "Input":
                                                  async def inp(e, i=nid):
                                                      if state["sel"]: await mgr.send_gm(state["sel"], i, e.value)
                                                      else: await mgr.broadcast_gm(i, e.value) # Fallback to broadcast
                                                  with ui.card().classes('bg-white border border-slate-200 p-3 h-[80px] justify-center gap-2 shadow-sm'):
                                                      ui.label(name).classes('text-[10px] font-bold text-slate-500 uppercase')
                                                      ui.input(on_change=inp).props('dense outlined input-style="font-size: 12px"').classes('w-full')

                        explorer = GMExplorer([])
                        mgr.on_gm_list = lambda s: explorer.update_tree(s)

                    with ui.tab_panel('CustomGM').classes('p-0'):
                         with ui.row().classes('w-full mb-4 justify-between items-center'):
                             ui.label('Stored Commands').classes('text-sm font-bold text-slate-700')
                             
                             with ui.dialog() as add_dlg, ui.card().classes('w-[400px] p-6 gap-4'):
                                 ui.label('New Custom Command').classes('text-lg font-bold')
                                 n_in = ui.input('Label').classes('w-full')
                                 c_in = ui.textarea('Lua Code').classes('w-full mono bg-slate-50')
                                 def save_c():
                                     if n_in.value and c_in.value:
                                         custom_mgr.add(n_in.value, c_in.value)
                                         render_custom()
                                         add_dlg.close()
                                 with ui.row().classes('w-full justify-end gap-2'):
                                     ui.button('Cancel', on_click=add_dlg.close).props('flat color=grey')
                                     ui.button('Save', on_click=save_c).props('unelevated color=primary')
                             
                             ui.button('Add Command', icon='add', on_click=add_dlg.open).props('unelevated dense color=primary size=sm').classes('px-3')

                         custom_grid = ui.grid().classes('w-full grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3')
                         
                         def render_custom():
                             custom_grid.clear()
                             with custom_grid:
                                 for idx, item in enumerate(custom_mgr.commands):
                                     with ui.card().classes('bg-white border border-slate-200 hover:border-indigo-400 group relative p-3 h-24 shadow-sm hover:shadow-md transition-all'):
                                         async def run_cust(c=item['cmd']):
                                             if state["sel"]: await mgr.send(state["sel"], c)
                                             else: await mgr.broadcast(c)
                                             ui.notify('Custom command sent', type='info', position='bottom')
                                         
                                         with ui.column().classes('w-full h-full justify-between cursor-pointer').on('click', run_cust):
                                             with ui.row().classes('w-full justify-between items-start'):
                                                 ui.icon('terminal', size='xs').classes('text-indigo-400')
                                                 ui.label(item['name']).classes('text-sm font-bold text-slate-800 truncate flex-1 ml-2')
                                             ui.label(item['cmd']).classes('text-[10px] mono text-slate-400 line-clamp-2 break-all')
                                         
                                         def del_c(i=idx):
                                             custom_mgr.delete(i)
                                             render_custom()
                                         ui.button(icon='close', on_click=del_c).props('flat dense round size=xs color=red').classes('absolute top-1 right-1 opacity-0 group-hover:opacity-100 transition-opacity')
                         
                         render_custom()

        # 3. Logs
        with ui.column().classes('w-[280px] h-full bg-white border-l border-slate-200 flex-none flex flex-col'):
            with ui.row().classes('w-full h-[40px] px-3 items-center justify-between border-b border-slate-100 bg-slate-50'):
                ui.label('SYSTEM LOGS').classes('text-[10px] font-bold text-slate-500 tracking-wider')
                ui.button(icon='delete_outline', on_click=lambda: log_container.clear()).props('flat dense round size=xs color=grey').tooltip('Clear Logs')
            log_container = ui.column().classes('w-full flex-1 overflow-y-auto overflow-x-hidden scroll-smooth gap-0 bg-white')

async def startup(): 
    # 在 Windows 平台上设置异常处理器（此时事件循环已创建）
    if sys.platform == 'win32':
        asyncio.get_running_loop().set_exception_handler(_windows_exception_handler)
    await mgr.add_listener(12581)
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
    kill_port_process(9529)   # Web UI 端口
    kill_port_process(12581)  # 游戏连接端口
    ui.run(title="GM Enterprise", host="0.0.0.0", port=9529, reload=False, favicon='🚀')