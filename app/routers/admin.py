"""Router Admin – Dashboard stratégique Tendo.

Outil de pilotage complet pour :
  - Data analyst : KPIs, tendances, graphiques, analyses de couverture
  - Financier : MRR, ARR, ARPU, conversions, projections, paiements
  - Opérationnel : scrapers, logs temps réel, déclencheurs, santé système

Auth : paramètre ?key= vérifié contre SECRET_KEY.
"""

import asyncio
import logging
from collections import deque
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import select, func, and_, delete, or_
from sqlalchemy.ext.asyncio import AsyncSession

from pydantic import BaseModel
from typing import Optional, List

from app.config import settings
from app.models.knowledge_base import KnowledgeBase
from app.models.notification import Notification
from app.models.publication import Publication
from app.models.subscription import Subscription
from app.models.user import User, SubscriptionStatus
from app.utils.db import get_db
from app.utils.logger import logger


# ── Schemas Pydantic pour Knowledge ──────────────────────────────────────────
class KnowledgeCreate(BaseModel):
    category: str
    subcategory: str = ""
    title: str
    content: str
    summary: Optional[str] = None
    keywords: Optional[List[str]] = None
    country: Optional[str] = "Benin"
    source_url: Optional[str] = None
    language: str = "fr"

class KnowledgeUpdate(BaseModel):
    category: Optional[str] = None
    subcategory: Optional[str] = None
    title: Optional[str] = None
    content: Optional[str] = None
    summary: Optional[str] = None
    keywords: Optional[List[str]] = None
    country: Optional[str] = None
    source_url: Optional[str] = None
    language: Optional[str] = None
    is_active: Optional[bool] = None

router = APIRouter(prefix="/admin", tags=["Admin"])

# ── Buffer de logs en mémoire ─────────────────────────────────────────────────
_log_buffer: deque = deque(maxlen=500)


class _BufHandler(logging.Handler):
    def emit(self, record):
        try:
            _log_buffer.append(self.format(record))
        except Exception:
            pass


_buf = _BufHandler()
_buf.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
logging.getLogger("tendo").addHandler(_buf)


# ── Auth ──────────────────────────────────────────────────────────────────────
def _ck(key: str = ""):
    if not key or key != settings.secret_key:
        raise HTTPException(status_code=403, detail="Clé admin invalide")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE HTML – Dashboard SPA
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/", response_class=HTMLResponse)
async def admin_page():
    return HTMLResponse(content=_DASHBOARD_HTML)


_DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="fr" class="dark">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Tendo – Centre de Pilotage</title>
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js" defer></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css"/>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
[x-cloak]{display:none!important}
*{font-family:'Inter',system-ui,sans-serif}
::-webkit-scrollbar{width:5px;height:5px}
::-webkit-scrollbar-track{background:#0B1121}
::-webkit-scrollbar-thumb{background:#1E293B;border-radius:6px}
::-webkit-scrollbar-thumb:hover{background:#334155}
body{background:#0B1121}
.sidebar{width:240px;background:#0F172A;border-right:1px solid #1E293B;min-height:100vh;position:fixed;left:0;top:0;z-index:40;transition:transform .2s}
.sidebar-link{display:flex;align-items:center;gap:10px;padding:10px 16px;border-radius:8px;margin:2px 8px;font-size:13px;color:#94A3B8;transition:all .15s;cursor:pointer}
.sidebar-link:hover{background:#1E293B;color:#E2E8F0}
.sidebar-link.active{background:#0EA5E9/15;color:#38BDF8;font-weight:500}
.sidebar-link.active i{color:#38BDF8}
.sidebar-link i{width:18px;text-align:center;font-size:14px;color:#64748B}
.main-area{margin-left:240px;min-height:100vh}
.card{background:#0F172A;border:1px solid #1E293B;border-radius:10px;padding:20px}
.card-header{font-size:13px;font-weight:600;color:#CBD5E1;margin-bottom:14px;display:flex;align-items:center;gap:8px}
.card-header i{color:#64748B;font-size:12px}
.kpi-card{background:linear-gradient(135deg,#0F172A 0%,#1E293B 100%);border:1px solid #1E293B;border-radius:10px;padding:18px;transition:border-color .2s}
.kpi-card:hover{border-color:#334155}
.kpi-icon{width:36px;height:36px;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:15px}
.kpi-value{font-size:26px;font-weight:700;letter-spacing:-0.5px;margin-top:10px}
.kpi-label{font-size:11px;color:#64748B;text-transform:uppercase;letter-spacing:0.5px;font-weight:500}
.kpi-sub{font-size:11px;color:#475569;margin-top:4px}
.badge{font-size:10px;padding:3px 8px;border-radius:6px;font-weight:600;letter-spacing:0.3px;text-transform:uppercase}
.b-trial{background:#854D0E20;color:#FACC15;border:1px solid #854D0E40}
.b-active{background:#06583920;color:#34D399;border:1px solid #06583940}
.b-expired{background:#7F1D1D20;color:#F87171;border:1px solid #7F1D1D40}
.b-ess{background:#1E3A5F20;color:#60A5FA;border:1px solid #1E3A5F40}
.b-prem{background:#4C1D9520;color:#A78BFA;border:1px solid #4C1D9540}
.b-paid{background:#06583920;color:#34D399;border:1px solid #06583940}
.b-pending{background:#854D0E20;color:#FACC15;border:1px solid #854D0E40}
.src-pill{font-size:10px;background:#1E293B;color:#94A3B8;padding:3px 8px;border-radius:5px;font-weight:500;border:1px solid #334155}
.input{background:#0F172A;border:1px solid #1E293B;border-radius:8px;padding:7px 12px;font-size:12px;color:#E2E8F0;outline:none;transition:border .15s}
.input:focus{border-color:#0EA5E9}
.btn{padding:7px 14px;border-radius:8px;font-size:12px;font-weight:500;cursor:pointer;transition:all .15s;display:inline-flex;align-items:center;gap:6px;border:none}
.btn-primary{background:#0EA5E9;color:white}.btn-primary:hover{background:#0284C7}
.btn-secondary{background:#1E293B;color:#CBD5E1;border:1px solid #334155}.btn-secondary:hover{background:#334155}
.btn-danger{background:#DC2626;color:white}.btn-danger:hover{background:#B91C1C}
.btn-success{background:#059669;color:white}.btn-success:hover{background:#047857}
.btn-purple{background:#7C3AED;color:white}.btn-purple:hover{background:#6D28D9}
.btn-warning{background:#D97706;color:white}.btn-warning:hover{background:#B45309}
.line-clamp-2{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.progress-track{height:5px;background:#1E293B;border-radius:4px;overflow:hidden}
.progress-fill{height:100%;border-radius:4px;transition:width .6s ease}
.alert{border-radius:8px;padding:10px 14px;font-size:12px;display:flex;align-items:flex-start;gap:10px}
.alert i{margin-top:1px}
.alert-danger{background:#7F1D1D15;border:1px solid #7F1D1D50;color:#FCA5A5}
.alert-warning{background:#854D0E15;border:1px solid #854D0E50;color:#FDE68A}
.alert-info{background:#1E3A5F15;border:1px solid #1E3A5F50;color:#93C5FD}
table{width:100%;font-size:12px;border-collapse:collapse}
thead th{text-align:left;padding:8px 10px;color:#64748B;font-weight:500;font-size:11px;text-transform:uppercase;letter-spacing:0.3px;border-bottom:1px solid #1E293B}
tbody td{padding:8px 10px;border-bottom:1px solid #1E293B20;color:#CBD5E1}
tbody tr:hover{background:#1E293B30}
@media(max-width:768px){.sidebar{transform:translateX(-100%)}.main-area{margin-left:0}}
</style>
<script>tailwind.config={darkMode:'class'}</script>
</head>
<body class="text-gray-300" x-data="dashboard()" x-init="boot()">

<!-- SIDEBAR -->
<aside class="sidebar flex flex-col" x-show="auth" x-cloak>
  <div class="px-5 py-5 border-b border-slate-800">
    <div class="flex items-center gap-3">
      <div class="w-9 h-9 rounded-lg bg-gradient-to-br from-sky-500 to-cyan-400 flex items-center justify-center shadow-lg shadow-sky-500/20">
        <i class="fa-solid fa-chart-line text-white text-sm"></i>
      </div>
      <div>
        <p class="font-bold text-white text-sm tracking-tight">Tendo</p>
        <p class="text-[10px] text-slate-500 font-medium">Centre de Pilotage</p>
      </div>
    </div>
  </div>
  <nav class="flex-1 py-3">
    <p class="text-[10px] text-slate-600 uppercase tracking-wider font-semibold px-5 mb-2">Navigation</p>
    <template x-for="t in tabs" :key="t.id">
      <div @click="tab=t.id" :class="tab===t.id?'sidebar-link active':'sidebar-link'">
        <i :class="t.icon"></i>
        <span x-text="t.label"></span>
      </div>
    </template>
  </nav>
  <div class="px-4 py-3 border-t border-slate-800">
    <div class="flex items-center justify-between">
      <span class="text-[10px] text-slate-600" x-text="lastRefresh?'MAJ '+lastRefresh:''"></span>
      <button @click="refreshAll()" :disabled="busy" class="text-slate-500 hover:text-sky-400 transition text-xs" title="Actualiser">
        <i class="fa-solid fa-arrows-rotate" :class="busy&&'fa-spin'"></i>
      </button>
    </div>
    <button @click="auth=false;apiKey='';localStorage.removeItem('tk')" class="mt-2 text-[11px] text-slate-600 hover:text-red-400 transition flex items-center gap-2 w-full">
      <i class="fa-solid fa-right-from-bracket"></i> Deconnexion
    </button>
  </div>
</aside>

<!-- LOGIN SCREEN -->
<div x-show="!auth" class="min-h-screen flex items-center justify-center" x-cloak>
  <div class="w-full max-w-sm">
    <div class="text-center mb-8">
      <div class="w-16 h-16 rounded-2xl bg-gradient-to-br from-sky-500 to-cyan-400 flex items-center justify-center mx-auto mb-4 shadow-xl shadow-sky-500/20">
        <i class="fa-solid fa-chart-line text-white text-2xl"></i>
      </div>
      <h1 class="text-xl font-bold text-white">Tendo Admin</h1>
      <p class="text-sm text-slate-500 mt-1">Centre de Pilotage Strategique</p>
    </div>
    <form @submit.prevent="login()" class="space-y-4">
      <div>
        <label class="text-[11px] text-slate-500 font-medium uppercase tracking-wider block mb-2">Cle d'administration</label>
        <div class="relative">
          <i class="fa-solid fa-key absolute left-3 top-1/2 -translate-y-1/2 text-slate-600 text-xs"></i>
          <input type="password" x-model="keyIn" placeholder="Entrez votre cle…" class="input w-full pl-9 py-3"/>
        </div>
      </div>
      <button type="submit" class="btn btn-primary w-full justify-center py-3"><i class="fa-solid fa-arrow-right-to-bracket"></i> Connexion</button>
    </form>
    <div x-show="errMsg" class="mt-4 alert alert-danger" x-cloak><i class="fa-solid fa-circle-xmark"></i><span x-text="errMsg"></span></div>
  </div>
</div>

<!-- MAIN CONTENT -->
<div class="main-area" x-show="auth" x-cloak>

  <!-- Top bar -->
  <header class="sticky top-0 z-30 bg-[#0B1121]/90 backdrop-blur-sm border-b border-slate-800 px-6 py-3 flex items-center justify-between">
    <div>
      <h1 class="text-base font-semibold text-white" x-text="tabs.find(t=>t.id===tab)?.label||''"></h1>
      <p class="text-[11px] text-slate-600">Shift Up &copy; 2026</p>
    </div>
    <div class="flex items-center gap-3">
      <span class="text-[10px] text-slate-600" x-text="lastRefresh?'Derniere MAJ: '+lastRefresh:''"></span>
      <button @click="refreshAll()" :disabled="busy" class="btn btn-secondary text-xs"><i class="fa-solid fa-arrows-rotate" :class="busy&&'fa-spin'"></i> Actualiser</button>
    </div>
  </header>

  <div class="p-6">

<!-- TAB: TABLEAU DE BORD -->
<section x-show="tab==='dash'" x-cloak>
  <!-- Alertes -->
  <div class="space-y-2 mb-5" x-show="alerts.length">
    <template x-for="(a,i) in alerts" :key="i">
      <div :class="'alert alert-'+a.level"><i :class="a.icon"></i><span x-html="a.html"></span></div>
    </template>
  </div>
  <!-- KPIs -->
  <div class="grid grid-cols-2 lg:grid-cols-5 gap-3 mb-5">
    <div class="kpi-card">
      <div class="flex items-center justify-between">
        <span class="kpi-label">Utilisateurs</span>
        <div class="kpi-icon bg-sky-500/10 text-sky-400"><i class="fa-solid fa-users"></i></div>
      </div>
      <p class="kpi-value text-white" x-text="S.total_users??'-'"></p>
      <p class="kpi-sub"><i class="fa-solid fa-circle text-emerald-400 text-[6px] mr-1"></i><span x-text="S.active_users_pct+'%'"></span> actifs</p>
    </div>
    <div class="kpi-card">
      <div class="flex items-center justify-between">
        <span class="kpi-label">Abonnes payants</span>
        <div class="kpi-icon bg-emerald-500/10 text-emerald-400"><i class="fa-solid fa-user-check"></i></div>
      </div>
      <p class="kpi-value text-emerald-400" x-text="S.paid_users??0"></p>
      <p class="kpi-sub"><i class="fa-solid fa-arrow-trend-up text-emerald-500 text-[9px] mr-1"></i><span x-text="S.conversion_rate+'%'"></span> conversion</p>
    </div>
    <div class="kpi-card">
      <div class="flex items-center justify-between">
        <span class="kpi-label">MRR</span>
        <div class="kpi-icon bg-amber-500/10 text-amber-400"><i class="fa-solid fa-coins"></i></div>
      </div>
      <p class="kpi-value text-amber-400" x-text="xof(S.mrr)"></p>
      <p class="kpi-sub">ARR: <span class="text-amber-300" x-text="xof(S.arr)"></span></p>
    </div>
    <div class="kpi-card">
      <div class="flex items-center justify-between">
        <span class="kpi-label">Publications</span>
        <div class="kpi-icon bg-violet-500/10 text-violet-400"><i class="fa-solid fa-file-lines"></i></div>
      </div>
      <p class="kpi-value text-white" x-text="S.total_pubs??'-'"></p>
      <p class="kpi-sub"><span x-text="S.sources_count??0"></span> sources <span class="text-slate-600 mx-1">|</span> <span x-text="S.pubs_with_pdf??0"></span> PDF</p>
    </div>
    <div class="kpi-card">
      <div class="flex items-center justify-between">
        <span class="kpi-label">Notifications</span>
        <div class="kpi-icon bg-sky-500/10 text-sky-400"><i class="fa-solid fa-bell"></i></div>
      </div>
      <p class="kpi-value text-sky-400" x-text="S.total_notifs??0"></p>
      <p class="kpi-sub">envoyees au total</p>
    </div>
  </div>

  <div class="grid lg:grid-cols-3 gap-4 mb-5">
    <div class="card">
      <div class="card-header"><i class="fa-solid fa-filter"></i> Entonnoir d'acquisition</div>
      <div class="space-y-3">
        <template x-for="step in funnel" :key="step.label">
          <div>
            <div class="flex justify-between text-[11px] mb-1"><span class="text-slate-400" x-text="step.label"></span><span class="text-white font-semibold font-mono" x-text="step.count"></span></div>
            <div class="progress-track"><div class="progress-fill bg-sky-500" :style="'width:'+step.pct+'%'"></div></div>
          </div>
        </template>
      </div>
    </div>
    <div class="card">
      <div class="card-header"><i class="fa-solid fa-user-pen"></i> Qualite des profils</div>
      <div class="space-y-3">
        <template x-for="p in profileMetrics" :key="p.label">
          <div>
            <div class="flex justify-between text-[11px] mb-1"><span class="text-slate-400" x-text="p.label"></span><span class="font-mono font-semibold" :class="p.pct>70?'text-emerald-400':p.pct>40?'text-amber-400':'text-red-400'" x-text="p.pct+'%'"></span></div>
            <div class="progress-track"><div class="progress-fill" :class="p.pct>70?'bg-emerald-500':p.pct>40?'bg-amber-500':'bg-red-500'" :style="'width:'+p.pct+'%'"></div></div>
          </div>
        </template>
      </div>
    </div>
    <div class="card">
      <div class="card-header"><i class="fa-solid fa-chart-pie"></i> Couverture sectorielle</div>
      <div class="space-y-2">
        <template x-for="s in sectorCoverage" :key="s.sector">
          <div class="flex items-center gap-2">
            <span class="text-[11px] text-slate-400 w-24 truncate" x-text="s.sector"></span>
            <div class="flex-1 progress-track"><div class="progress-fill bg-violet-500" :style="'width:'+s.pct+'%'"></div></div>
            <span class="text-[11px] font-mono w-7 text-right text-slate-500" x-text="s.count"></span>
          </div>
        </template>
      </div>
    </div>
  </div>

  <div class="grid lg:grid-cols-2 gap-4 mb-5">
    <div class="card"><div class="card-header"><i class="fa-solid fa-chart-bar"></i> Inscriptions par jour</div><div class="h-48"><canvas id="chartUsers"></canvas></div></div>
    <div class="card"><div class="card-header"><i class="fa-solid fa-chart-pie"></i> Publications par source</div><div class="h-48"><canvas id="chartSources"></canvas></div></div>
  </div>

  <div class="card">
    <div class="card-header"><i class="fa-solid fa-user-plus"></i> Inscriptions recentes</div>
    <table>
      <thead><tr><th>Nom</th><th>Entreprise</th><th>Telephone</th><th>Statut</th><th>Inscription</th></tr></thead>
      <tbody>
        <template x-for="u in (S.recent_users??[]).slice(0,10)" :key="u.id">
          <tr>
            <td class="text-white font-medium" x-text="u.name||'(Sans nom)'"></td>
            <td class="text-slate-500" x-text="u.company||'-'"></td>
            <td class="font-mono text-slate-400 text-[11px]" x-text="u.phone_number"></td>
            <td><span class="badge" :class="bc(u.subscription_status)" x-text="u.subscription_status"></span></td>
            <td class="text-slate-500 text-[11px]" x-text="fdate(u.created_at)"></td>
          </tr>
        </template>
      </tbody>
    </table>
  </div>
</section>

<!-- TAB: FINANCE -->
<section x-show="tab==='finance'" x-cloak>
  <div class="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-5">
    <div class="kpi-card" style="border-color:#854D0E40">
      <div class="flex items-center justify-between"><span class="kpi-label">MRR</span><div class="kpi-icon bg-amber-500/10 text-amber-400"><i class="fa-solid fa-chart-line"></i></div></div>
      <p class="kpi-value text-amber-400" x-text="xof(S.mrr)"></p>
      <p class="kpi-sub text-[10px]">(<span x-text="S.active_essentiel??0"></span> x <span x-text="xof(S.price_essentiel)"></span>) + (<span x-text="S.active_premium??0"></span> x <span x-text="xof(S.price_premium)"></span>)</p>
    </div>
    <div class="kpi-card" style="border-color:#854D0E40">
      <div class="flex items-center justify-between"><span class="kpi-label">ARR (projete)</span><div class="kpi-icon bg-amber-500/10 text-amber-300"><i class="fa-solid fa-calendar-check"></i></div></div>
      <p class="kpi-value text-amber-300" x-text="xof(S.arr)"></p>
      <p class="kpi-sub">MRR x 12</p>
    </div>
    <div class="kpi-card">
      <div class="flex items-center justify-between"><span class="kpi-label">ARPU</span><div class="kpi-icon bg-sky-500/10 text-sky-400"><i class="fa-solid fa-user-tag"></i></div></div>
      <p class="kpi-value text-white" x-text="xof(S.arpu)"></p>
      <p class="kpi-sub">Revenu moyen / payant</p>
    </div>
    <div class="kpi-card">
      <div class="flex items-center justify-between"><span class="kpi-label">Revenu Total</span><div class="kpi-icon bg-emerald-500/10 text-emerald-400"><i class="fa-solid fa-vault"></i></div></div>
      <p class="kpi-value text-emerald-400" x-text="xof(S.revenue_total)"></p>
      <p class="kpi-sub"><span x-text="S.paid_subs_count??0"></span> paiements</p>
    </div>
  </div>
  <div class="grid lg:grid-cols-2 gap-4 mb-5">
    <div class="card">
      <div class="card-header"><i class="fa-solid fa-tags"></i> Tarifs actuels</div>
      <div class="space-y-3">
        <div class="flex items-center justify-between p-3 bg-slate-800/40 rounded-lg border border-slate-700/30">
          <div class="flex items-center gap-3"><div class="w-8 h-8 rounded-lg bg-sky-500/10 flex items-center justify-center"><i class="fa-solid fa-star text-sky-400 text-xs"></i></div><div><span class="badge b-ess">Essentiel</span><p class="text-[11px] text-slate-500 mt-1"><span x-text="S.active_essentiel??0"></span> abonnes actifs</p></div></div>
          <span class="text-xl font-bold font-mono text-sky-400" x-text="xof(S.price_essentiel)"></span>
        </div>
        <div class="flex items-center justify-between p-3 bg-slate-800/40 rounded-lg border border-slate-700/30">
          <div class="flex items-center gap-3"><div class="w-8 h-8 rounded-lg bg-violet-500/10 flex items-center justify-center"><i class="fa-solid fa-crown text-violet-400 text-xs"></i></div><div><span class="badge b-prem">Premium</span><p class="text-[11px] text-slate-500 mt-1"><span x-text="S.active_premium??0"></span> abonnes actifs</p></div></div>
          <span class="text-xl font-bold font-mono text-violet-400" x-text="xof(S.price_premium)"></span>
        </div>
      </div>
    </div>
    <div class="card">
      <div class="card-header"><i class="fa-solid fa-hourglass-half"></i> Essais en cours — Potentiel de conversion</div>
      <div class="space-y-2 max-h-64 overflow-y-auto">
        <template x-for="u in trialUsers" :key="u.id">
          <div class="flex items-center justify-between text-[12px] bg-slate-800/30 rounded-lg px-3 py-2.5 border border-slate-700/20">
            <div class="flex items-center gap-2"><i class="fa-solid fa-user text-slate-600 text-[10px]"></i><span class="text-slate-200" x-text="u.name||u.phone_number"></span><span class="text-slate-600 text-[11px]" x-text="u.company?'('+u.company+')':''"></span></div>
            <span class="font-mono font-semibold text-[11px]" :class="u.days_left<=2?'text-red-400':u.days_left<=5?'text-amber-400':'text-slate-500'" x-text="u.days_left+'j'"></span>
          </div>
        </template>
        <p x-show="!trialUsers.length" class="text-slate-600 text-xs italic py-2">Aucun essai en cours</p>
      </div>
    </div>
  </div>
  <div class="card">
    <div class="card-header"><i class="fa-solid fa-receipt"></i> Historique des paiements</div>
    <div class="overflow-x-auto">
      <table>
        <thead><tr><th>Telephone</th><th>Plan</th><th class="text-right">Montant</th><th>Statut</th><th>Ref. transaction</th><th>Debut</th><th>Fin</th></tr></thead>
        <tbody>
          <template x-for="p in (S.all_payments??[])" :key="p.id">
            <tr>
              <td class="font-mono text-[11px]" x-text="p.phone||'-'"></td>
              <td><span class="badge" :class="p.plan==='premium'?'b-prem':'b-ess'" x-text="p.plan||'-'"></span></td>
              <td class="text-right font-mono text-amber-400" x-text="xof(p.amount)"></td>
              <td><span class="badge" :class="p.status==='paid'?'b-paid':'b-pending'" x-text="p.status"></span></td>
              <td class="font-mono text-slate-600 text-[11px]" x-text="p.transaction_id||'-'"></td>
              <td class="text-slate-500 text-[11px]" x-text="fdate(p.start_date)"></td>
              <td class="text-slate-500 text-[11px]" x-text="fdate(p.end_date)"></td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
  </div>
</section>

<!-- TAB: MARCHE -->
<section x-show="tab==='market'" x-cloak>
  <div class="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-5">
    <div class="kpi-card"><div class="flex items-center justify-between"><span class="kpi-label">Publications</span><div class="kpi-icon bg-violet-500/10 text-violet-400"><i class="fa-solid fa-file-lines"></i></div></div><p class="kpi-value text-white" x-text="S.total_pubs??0"></p></div>
    <div class="kpi-card"><div class="flex items-center justify-between"><span class="kpi-label">Sources actives</span><div class="kpi-icon bg-sky-500/10 text-sky-400"><i class="fa-solid fa-globe"></i></div></div><p class="kpi-value text-sky-400" x-text="S.sources_count??0"></p></div>
    <div class="kpi-card"><div class="flex items-center justify-between"><span class="kpi-label">Avec PDF</span><div class="kpi-icon bg-emerald-500/10 text-emerald-400"><i class="fa-solid fa-file-pdf"></i></div></div><p class="kpi-value text-emerald-400" x-text="S.pubs_with_pdf??0"></p><p class="kpi-sub" x-text="S.total_pubs?Math.round(S.pubs_with_pdf/S.total_pubs*100)+'% couverture':''"></p></div>
    <div class="kpi-card"><div class="flex items-center justify-between"><span class="kpi-label">Non classifiees</span><div class="kpi-icon bg-red-500/10 text-red-400"><i class="fa-solid fa-circle-question"></i></div></div><p class="kpi-value" :class="S.unclassified_pubs>20?'text-red-400':'text-amber-400'" x-text="S.unclassified_pubs??0"></p></div>
  </div>
  <div class="grid lg:grid-cols-2 gap-4 mb-5">
    <div class="card"><div class="card-header"><i class="fa-solid fa-sitemap"></i> Repartition par source</div><div class="space-y-2"><template x-for="s in (S.publications_by_source??[])" :key="s.source"><div class="flex items-center gap-2"><span class="text-[11px] text-slate-300 w-32 truncate font-medium" x-text="s.source"></span><div class="flex-1 progress-track"><div class="progress-fill bg-sky-500" :style="'width:'+Math.min(100,s.count/(S.total_pubs||1)*100)+'%'"></div></div><span class="text-[11px] font-mono w-7 text-right text-slate-500" x-text="s.count"></span></div></template></div></div>
    <div class="card"><div class="card-header"><i class="fa-solid fa-folder-tree"></i> Repartition par type</div><div class="space-y-2"><template x-for="d in (S.pubs_by_type??[])" :key="d.type"><div class="flex items-center gap-2"><span class="text-[11px] text-slate-300 w-32 truncate font-medium" x-text="d.type||'Non classe'"></span><div class="flex-1 progress-track"><div class="progress-fill bg-violet-500" :style="'width:'+Math.min(100,d.count/(S.total_pubs||1)*100)+'%'"></div></div><span class="text-[11px] font-mono w-7 text-right text-slate-500" x-text="d.count"></span></div></template></div></div>
  </div>
  <div class="card mb-5">
    <div class="card-header"><i class="fa-solid fa-scale-balanced"></i> Demande vs Offre sectorielle</div>
    <table>
      <thead><tr><th>Secteur</th><th class="text-right">Demande users</th><th class="text-right">Publications</th><th>Gap</th></tr></thead>
      <tbody><template x-for="g in sectorGaps" :key="g.sector"><tr><td class="text-white font-medium" x-text="g.sector"></td><td class="text-right font-mono" x-text="g.demand"></td><td class="text-right font-mono" x-text="g.supply"></td><td><span class="badge" :class="g.gap==='OK'?'b-active':g.gap==='Faible'?'b-trial':'b-expired'" x-text="g.gap"></span></td></tr></template></tbody>
    </table>
  </div>
  <div class="card">
    <div class="card-header"><i class="fa-solid fa-magnifying-glass"></i> Publications</div>
    <div class="flex gap-2 mb-4 flex-wrap items-center">
      <div class="relative"><i class="fa-solid fa-search absolute left-3 top-1/2 -translate-y-1/2 text-slate-600 text-[10px]"></i><input type="text" x-model="pubQ" placeholder="Rechercher…" class="input w-52 pl-8"/></div>
      <select x-model="pubSrcF" class="input"><option value="">Toutes sources</option><template x-for="s in pubSrcOpts"><option :value="s" x-text="s"></option></template></select>
      <select x-model="pubTypeF" class="input"><option value="">Tous types</option><template x-for="t in pubTypeOpts"><option :value="t" x-text="t"></option></template></select>
      <span class="text-slate-600 text-[11px] ml-1" x-text="fPubs.length+' resultats'"></span>
    </div>
    <div class="max-h-[500px] overflow-y-auto">
      <table><thead class="sticky top-0 bg-[#0F172A]"><tr><th class="w-10">ID</th><th class="w-24">Source</th><th class="w-20">Type</th><th>Titre</th><th class="w-24">Budget</th><th class="w-20">Deadline</th><th class="w-28">Actions</th></tr></thead>
        <tbody><template x-for="p in fPubs.slice(0,200)" :key="p.id"><tr>
          <td class="font-mono text-slate-600" x-text="p.id"></td>
          <td><span class="src-pill" x-text="p.source"></span></td>
          <td>
            <span x-show="!p._editing" class="text-sky-400 text-[11px] cursor-pointer hover:underline" @click="p._editing=true;p._newType=p.document_type||''" x-text="p.document_type||'Non classé'"></span>
            <div x-show="p._editing" class="flex gap-1">
              <select x-model="p._newType" class="input text-[10px] py-0.5 w-28">
                <option value="">Non classé</option>
                <option value="AAO">AAO</option><option value="DAO">DAO</option>
                <option value="AMI">AMI</option><option value="RFQ">RFQ</option><option value="RFP">RFP</option>
                <option value="PV_ATTRIBUTION">PV Attribution</option><option value="PV_OUVERTURE">PV Ouverture</option>
                <option value="AVIS_ATTRIBUTION">Avis Attribution</option>
                <option value="DECISION_ARMP">Decision ARMP</option>
                <option value="PPM">PPM</option><option value="ADDITIF">Additif</option>
                <option value="LISTE_RESTREINTE">Liste Restreinte</option>
              </select>
              <button @click="classifyPub(p)" class="text-emerald-400 hover:text-emerald-300"><i class="fa-solid fa-check text-[10px]"></i></button>
              <button @click="p._editing=false" class="text-slate-500 hover:text-slate-300"><i class="fa-solid fa-xmark text-[10px]"></i></button>
            </div>
          </td>
          <td class="text-slate-200 max-w-sm truncate cursor-pointer hover:text-sky-400" @click="openPubDetail(p)" x-text="p.title"></td>
          <td class="font-mono text-amber-400" x-text="p.budget?xof(p.budget):'-'"></td>
          <td class="text-slate-500 text-[11px]" x-text="fdate(p.deadline)"></td>
          <td class="flex gap-1 items-center">
            <button @click="openPubDetail(p)" class="text-sky-500 hover:text-sky-300 transition" title="Voir details"><i class="fa-solid fa-eye text-[10px]"></i></button>
            <a x-show="p.pdf_url" :href="p.pdf_url" target="_blank" class="text-violet-400 hover:text-violet-300 transition" title="Ouvrir PDF"><i class="fa-solid fa-file-pdf text-[10px]"></i></a>
            <button @click="delPub(p)" class="text-slate-600 hover:text-red-400 transition" title="Supprimer"><i class="fa-solid fa-trash-can text-[10px]"></i></button>
          </td>
        </tr></template></tbody>
      </table>
    </div>
  </div>

  <!-- Modal détail publication -->
  <div x-show="pubDetail" x-cloak class="fixed inset-0 bg-black/70 z-50 flex items-center justify-center p-4" @click.self="pubDetail=null">
    <div class="bg-[#1E293B] rounded-xl border border-slate-700 w-full max-w-3xl max-h-[85vh] overflow-y-auto shadow-2xl" x-show="pubDetail">
      <div class="sticky top-0 bg-[#1E293B] border-b border-slate-700 px-5 py-3 flex justify-between items-center z-10">
        <h3 class="text-white font-semibold text-sm" x-text="pubDetail?.title"></h3>
        <button @click="pubDetail=null" class="text-slate-400 hover:text-white"><i class="fa-solid fa-xmark"></i></button>
      </div>
      <div class="p-5 space-y-4">
        <div class="grid grid-cols-2 lg:grid-cols-3 gap-3">
          <div><span class="text-[10px] text-slate-500 uppercase">Source</span><p class="text-white text-sm" x-text="pubDetail?.source"></p></div>
          <div><span class="text-[10px] text-slate-500 uppercase">Référence</span><p class="text-white text-sm font-mono" x-text="pubDetail?.reference"></p></div>
          <div><span class="text-[10px] text-slate-500 uppercase">Type document</span><p class="text-sky-400 text-sm" x-text="pubDetail?.document_type||'Non classé'"></p></div>
          <div><span class="text-[10px] text-slate-500 uppercase">Budget</span><p class="text-amber-400 text-sm font-mono" x-text="pubDetail?.budget?xof(pubDetail.budget):'Non spécifié'"></p></div>
          <div><span class="text-[10px] text-slate-500 uppercase">Deadline</span><p class="text-sm" :class="pubDetail?.deadline?'text-red-400':'text-slate-600'" x-text="pubDetail?.deadline?fdate(pubDetail.deadline):'Non spécifiée'"></p></div>
          <div><span class="text-[10px] text-slate-500 uppercase">Autorité</span><p class="text-white text-sm" x-text="pubDetail?.authority_name||'Non renseignée'"></p></div>
          <div><span class="text-[10px] text-slate-500 uppercase">Financement</span><p class="text-white text-sm" x-text="pubDetail?.financing_source||'-'"></p></div>
          <div><span class="text-[10px] text-slate-500 uppercase">Pays</span><p class="text-white text-sm" x-text="pubDetail?.country||'Bénin'"></p></div>
          <div><span class="text-[10px] text-slate-500 uppercase">Secteurs</span><p class="text-white text-sm" x-text="(pubDetail?.sectors||[]).join(', ')||'-'"></p></div>
        </div>
        <template x-if="pubDetail?.pdf_url">
          <div><span class="text-[10px] text-slate-500 uppercase">Document PDF</span><a :href="pubDetail.pdf_url" target="_blank" class="block text-violet-400 text-sm hover:underline break-all"><i class="fa-solid fa-file-pdf mr-1"></i><span x-text="pubDetail.pdf_url"></span></a></div>
        </template>
        <template x-if="pubDetail?.technical_summary">
          <div class="bg-slate-800/50 rounded-lg p-4 border border-slate-700/30"><span class="text-[10px] text-slate-500 uppercase block mb-2">Résumé IA</span><p class="text-slate-300 text-[12px] leading-relaxed whitespace-pre-wrap" x-text="pubDetail.technical_summary"></p></div>
        </template>
        <template x-if="pubDetail?.required_documents?.length">
          <div><span class="text-[10px] text-slate-500 uppercase block mb-1">Documents requis</span><ul class="list-disc ml-4 text-[12px] text-slate-400"><template x-for="d in pubDetail.required_documents"><li x-text="d"></li></template></ul></div>
        </template>
        <template x-if="pubDetail?.qualification_criteria?.length">
          <div><span class="text-[10px] text-slate-500 uppercase block mb-1">Critères de qualification</span><ul class="list-disc ml-4 text-[12px] text-slate-400"><template x-for="c in pubDetail.qualification_criteria"><li x-text="c"></li></template></ul></div>
        </template>
        <template x-if="pubDetail?.guarantee_amount">
          <div><span class="text-[10px] text-slate-500 uppercase">Garantie de soumission</span><p class="text-emerald-400 text-sm font-mono" x-text="xof(pubDetail.guarantee_amount)"></p></div>
        </template>
        <template x-if="pubDetail?.html_content">
          <div class="bg-slate-800/50 rounded-lg p-4 border border-slate-700/30 max-h-60 overflow-y-auto"><span class="text-[10px] text-slate-500 uppercase block mb-2">Contenu extrait</span><div class="text-slate-300 text-[11px] leading-relaxed whitespace-pre-wrap" x-text="pubDetail.html_content?.substring(0,3000)"></div></div>
        </template>
      </div>
    </div>
  </div>
</section>

<!-- TAB: UTILISATEURS -->
<section x-show="tab==='users'" x-cloak>
  <div class="flex gap-2 mb-4 flex-wrap items-center">
    <div class="relative"><i class="fa-solid fa-search absolute left-3 top-1/2 -translate-y-1/2 text-slate-600 text-[10px]"></i><input type="text" x-model="usrQ" placeholder="Nom, telephone, entreprise…" class="input w-56 pl-8"/></div>
    <select x-model="usrF" class="input"><option value="">Tous statuts</option><option value="trial">Essai</option><option value="active">Actif</option><option value="expired">Expire</option></select>
    <span class="text-slate-600 text-[11px] ml-1" x-text="fUsers.length+' utilisateurs'"></span>
  </div>
  <div class="card overflow-x-auto">
    <table>
      <thead><tr><th>ID</th><th>Telephone</th><th>Nom</th><th>Entreprise</th><th>Email</th><th>Statut</th><th>Plan</th><th>Trial</th><th>Secteurs</th><th>Inscription</th><th>Fin essai</th><th>Actions</th></tr></thead>
      <tbody>
        <template x-for="u in fUsers" :key="u.id">
          <tr>
            <td class="font-mono text-slate-600 text-[11px]" x-text="u.id"></td>
            <td class="font-mono text-[11px]" x-text="u.phone_number"></td>
            <td class="text-white font-medium" x-text="u.name||'-'"></td>
            <td class="text-slate-500" x-text="u.company||'-'"></td>
            <td class="text-slate-500 text-[11px]" x-text="u.email_address||'-'"></td>
            <td><span class="badge" :class="bc(u.subscription_status)" x-text="u.subscription_status"></span></td>
            <td><span x-show="u.subscription_plan" class="badge" :class="u.subscription_plan==='premium'?'b-prem':'b-ess'" x-text="u.subscription_plan"></span></td>
            <td><span class="badge text-[9px]" :class="u.has_used_trial?'bg-amber-500/20 text-amber-400':'bg-emerald-500/20 text-emerald-400'" x-text="u.has_used_trial?'Utilisé':'Disponible'"></span></td>
            <td class="text-slate-500 text-[11px] max-w-[100px] truncate" x-text="(u.sectors||[]).join(', ')||'-'"></td>
            <td class="text-slate-500 text-[11px]" x-text="fdate(u.created_at)"></td>
            <td class="text-slate-500 text-[11px]" x-text="fdate(u.trial_end)"></td>
            <td>
              <button @click="toggleUsr(u)" class="btn text-[10px] py-1 px-2" :class="u.is_active?'btn-danger':'btn-success'">
                <i :class="u.is_active?'fa-solid fa-ban':'fa-solid fa-check'"></i>
                <span x-text="u.is_active?'Desactiver':'Activer'"></span>
              </button>
            </td>
          </tr>
        </template>
      </tbody>
    </table>
  </div>
</section>

<!-- TAB: OPERATIONS -->
<section x-show="tab==='ops'" x-cloak>
  <div class="grid lg:grid-cols-2 gap-4 mb-5">
    <div class="card">
      <div class="card-header"><i class="fa-solid fa-clock"></i> Scheduler <span class="ml-auto text-[10px]" :class="sys.scheduler_running?'text-emerald-400':'text-red-400'" x-text="sys.scheduler_running?'Actif':'Arrete'"></span></div>
      <div class="space-y-2">
        <template x-for="j in (sys.jobs??[])" :key="j.id">
          <div class="flex justify-between items-center text-[12px] bg-slate-800/30 rounded-lg px-3 py-2.5 border border-slate-700/20">
            <div class="flex items-center gap-2"><i class="fa-solid fa-gear text-slate-600 text-[10px]"></i><span class="text-slate-200 font-medium" x-text="j.name"></span></div>
            <span class="text-slate-500 text-[11px]" x-text="j.next_run?fdate(j.next_run):'-'"></span>
          </div>
        </template>
      </div>
    </div>
    <div class="card">
      <div class="card-header"><i class="fa-solid fa-bolt"></i> Declencheurs manuels</div>
      <div class="space-y-2">
        <div class="flex items-center justify-between bg-slate-800/30 rounded-lg px-3 py-2.5 border border-slate-700/20">
          <div><p class="text-[12px] text-slate-200 font-medium flex items-center gap-2"><i class="fa-solid fa-spider text-slate-600 text-[10px]"></i> Scraping</p><p class="text-[10px] text-slate-600 mt-0.5 ml-5">ARMP, JNMP, BAD, ADPME, gouv.bj…</p></div>
          <button @click="trig('scraping')" :disabled="trigging.scraping" class="btn btn-primary text-[11px]"><i class="fa-solid fa-play" :class="trigging.scraping&&'fa-spin'"></i> Lancer</button>
        </div>
        <div class="flex items-center justify-between bg-slate-800/30 rounded-lg px-3 py-2.5 border border-slate-700/20">
          <div><p class="text-[12px] text-slate-200 font-medium flex items-center gap-2"><i class="fa-brands fa-whatsapp text-slate-600 text-[10px]"></i> Notifications WhatsApp</p><p class="text-[10px] text-slate-600 mt-0.5 ml-5">Envoyer aux abonnes correspondants</p></div>
          <button @click="trig('notifications')" :disabled="trigging.notifications" class="btn btn-primary text-[11px]"><i class="fa-solid fa-play" :class="trigging.notifications&&'fa-spin'"></i> Lancer</button>
        </div>
        <div class="flex items-center justify-between bg-slate-800/30 rounded-lg px-3 py-2.5 border border-slate-700/20">
          <div><p class="text-[12px] text-emerald-300 font-medium flex items-center gap-2"><i class="fa-solid fa-newspaper text-emerald-600 text-[10px]"></i> Analyse JNMP</p><p class="text-[10px] text-slate-600 mt-0.5 ml-5">Segmenter les journaux PDF en documents</p></div>
          <button @click="trig('jnmp')" :disabled="trigging.jnmp" class="btn btn-success text-[11px]"><i class="fa-solid fa-play" :class="trigging.jnmp&&'fa-spin'"></i> Lancer</button>
        </div>
        <div class="flex items-center justify-between bg-slate-800/30 rounded-lg px-3 py-2.5 border border-slate-700/20">
          <div><p class="text-[12px] text-slate-200 font-medium flex items-center gap-2"><i class="fa-solid fa-robot text-slate-600 text-[10px]"></i> Pipeline PDF (IA)</p><p class="text-[10px] text-slate-600 mt-0.5 ml-5">Extraire texte + classifier + resumer par IA</p></div>
          <button @click="trig('pdf-processing')" :disabled="trigging['pdf-processing']" class="btn btn-purple text-[11px]"><i class="fa-solid fa-play" :class="trigging['pdf-processing']&&'fa-spin'"></i> Lancer</button>
        </div>
        <div class="flex items-center justify-between bg-slate-800/30 rounded-lg px-3 py-2.5 border border-slate-700/20">
          <div><p class="text-[12px] text-amber-300 font-medium flex items-center gap-2"><i class="fa-solid fa-broom text-amber-600 text-[10px]"></i> Nettoyage AO expires</p><p class="text-[10px] text-slate-600 mt-0.5 ml-5">Supprimer les AO dont la deadline est passee</p></div>
          <button @click="trig('cleanup-expired')" :disabled="trigging['cleanup-expired']" class="btn btn-warning text-[11px]"><i class="fa-solid fa-play" :class="trigging['cleanup-expired']&&'fa-spin'"></i> Lancer</button>
        </div>
        <div class="flex items-center justify-between bg-slate-800/30 rounded-lg px-3 py-2.5 border border-slate-700/20">
          <div><p class="text-[12px] text-purple-300 font-medium flex items-center gap-2"><i class="fa-solid fa-brain text-purple-600 text-[10px]"></i> Enrichissement IA</p><p class="text-[10px] text-slate-600 mt-0.5 ml-5">DeepSeek analyse et resume les publications</p></div>
          <button @click="trig('enrich-publications')" :disabled="trigging['enrich-publications']" class="btn btn-purple text-[11px]"><i class="fa-solid fa-play" :class="trigging['enrich-publications']&&'fa-spin'"></i> Lancer</button>
        </div>
        <div class="flex items-center justify-between bg-slate-800/30 rounded-lg px-3 py-2.5 border border-slate-700/20">
          <div><p class="text-[12px] text-cyan-300 font-medium flex items-center gap-2"><i class="fa-solid fa-comments text-cyan-600 text-[10px]"></i> Discussion proactive</p><p class="text-[10px] text-slate-600 mt-0.5 ml-5">Tendo envoie un message proactif aux utilisateurs</p></div>
          <button @click="trig('proactive-discussion')" :disabled="trigging['proactive-discussion']" class="btn btn-primary text-[11px]"><i class="fa-solid fa-play" :class="trigging['proactive-discussion']&&'fa-spin'"></i> Lancer</button>
        </div>
      </div>
      <div x-show="trigMsg" class="mt-3 text-[11px] text-emerald-400 bg-emerald-900/20 rounded-lg px-3 py-2 border border-emerald-800/30 flex items-center gap-2" x-cloak><i class="fa-solid fa-circle-check"></i><span x-text="trigMsg"></span></div>
    </div>
  </div>
  <div class="card mb-4">
    <div class="card-header"><i class="fa-solid fa-sliders"></i> Configuration systeme</div>
    <div class="grid grid-cols-2 md:grid-cols-3 gap-2">
      <template x-for="[k,v] in Object.entries(sys.env??{})" :key="k">
        <div class="bg-slate-800/30 rounded-lg px-3 py-2.5 border border-slate-700/20"><p class="text-[9px] text-slate-600 uppercase tracking-wider font-semibold" x-text="k"></p><p class="text-slate-300 text-[11px] font-mono truncate mt-0.5" x-text="v"></p></div>
      </template>
    </div>
  </div>
  <div class="card">
    <div class="card-header flex items-center justify-between w-full">
      <div class="flex items-center gap-2"><i class="fa-solid fa-terminal"></i> Logs temps reel</div>
      <div class="flex gap-2">
        <button @click="loadLogs()" class="btn btn-secondary text-[10px]"><i class="fa-solid fa-arrows-rotate"></i></button>
        <button @click="autoLog=!autoLog" :class="autoLog?'btn btn-primary':'btn btn-secondary'" class="text-[10px]"><i :class="autoLog?'fa-solid fa-pause':'fa-solid fa-play'"></i> Auto</button>
      </div>
    </div>
    <div class="bg-[#0B1121] rounded-lg p-3 h-80 overflow-y-auto font-mono text-[10px] leading-relaxed border border-slate-800">
      <template x-for="(l,i) in logs" :key="i">
        <div class="py-0.5" :class="l.includes('ERROR')?'text-red-400':l.includes('WARNING')?'text-amber-400':l.includes('INFO')?'text-slate-400':'text-slate-600'" x-text="l"></div>
      </template>
      <p x-show="!logs.length" class="text-slate-700 italic">Aucun log</p>
    </div>
  </div>
</section>

<!-- TAB: CONNAISSANCES -->
<section x-show="tab==='knowledge'" x-cloak>
  <div class="flex items-center justify-between mb-4">
    <h2 class="text-lg font-semibold text-slate-200"><i class="fa-solid fa-book mr-2 text-cyan-400"></i>Base de Connaissances</h2>
    <div class="flex gap-2">
      <button @click="kbSeed()" class="btn btn-secondary text-[11px]"><i class="fa-solid fa-database"></i> Peupler initiale</button>
      <button @click="kbLearn()" class="btn btn-purple text-[11px]"><i class="fa-solid fa-brain"></i> Auto-apprentissage</button>
      <button @click="kbNew()" class="btn btn-primary text-[11px]"><i class="fa-solid fa-plus"></i> Ajouter</button>
    </div>
  </div>
  <div x-show="kbMsg" class="mb-3 text-[11px] text-emerald-400 bg-emerald-900/20 rounded-lg px-3 py-2 border border-emerald-800/30 flex items-center gap-2" x-cloak><i class="fa-solid fa-circle-check"></i><span x-text="kbMsg"></span></div>

  <!-- Formulaire ajout/edition -->
  <div x-show="kbForm.title!==undefined && (kbEdit!==null || kbForm.category!=='' || kbForm.title!=='')" class="card mb-4" x-cloak>
    <div class="card-header"><i class="fa-solid fa-pen"></i> <span x-text="kbEdit?'Modifier':'Ajouter'"></span> une connaissance</div>
    <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
      <div><label class="text-[10px] text-slate-500 block mb-1">Categorie *</label>
        <select x-model="kbForm.category" class="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1.5 text-[12px] text-slate-200">
          <option value="">-- Choisir --</option>
          <option value="document_type">Type de document</option>
          <option value="procedure">Procedure</option>
          <option value="reglementation">Reglementation</option>
          <option value="source_info">Source info</option>
          <option value="lexique">Lexique</option>
          <option value="intelligence_marche">Intelligence marche</option>
          <option value="conseil_pratique">Conseil pratique</option>
          <option value="auto_learn">Auto-apprentissage</option>
        </select>
      </div>
      <div><label class="text-[10px] text-slate-500 block mb-1">Sous-categorie</label><input x-model="kbForm.subcategory" class="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1.5 text-[12px] text-slate-200" placeholder="Ex: AAO, DAO..."></div>
      <div><label class="text-[10px] text-slate-500 block mb-1">Pays</label><input x-model="kbForm.country" class="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1.5 text-[12px] text-slate-200" placeholder="Benin"></div>
      <div><label class="text-[10px] text-slate-500 block mb-1">Langue</label><input x-model="kbForm.language" class="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1.5 text-[12px] text-slate-200" placeholder="fr"></div>
    </div>
    <div class="mb-3"><label class="text-[10px] text-slate-500 block mb-1">Titre *</label><input x-model="kbForm.title" class="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1.5 text-[12px] text-slate-200" placeholder="Titre de la connaissance"></div>
    <div class="mb-3"><label class="text-[10px] text-slate-500 block mb-1">Contenu *</label><textarea x-model="kbForm.content" rows="5" class="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1.5 text-[12px] text-slate-200" placeholder="Contenu detaille de la connaissance..."></textarea></div>
    <div class="grid grid-cols-2 gap-3 mb-3">
      <div><label class="text-[10px] text-slate-500 block mb-1">Resume (optionnel)</label><textarea x-model="kbForm.summary" rows="2" class="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1.5 text-[12px] text-slate-200" placeholder="Resume court..."></textarea></div>
      <div><label class="text-[10px] text-slate-500 block mb-1">Mots-cles (separes par virgule)</label><input x-model="kbForm.keywords" class="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1.5 text-[12px] text-slate-200" placeholder="mot1, mot2, mot3"><label class="text-[10px] text-slate-500 block mt-2 mb-1">URL source (optionnel)</label><input x-model="kbForm.source_url" class="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1.5 text-[12px] text-slate-200" placeholder="https://..."></div>
    </div>
    <div class="flex gap-2">
      <button @click="kbSave()" class="btn btn-primary text-[11px]"><i class="fa-solid fa-save"></i> <span x-text="kbEdit?'Mettre a jour':'Enregistrer'"></span></button>
      <button @click="kbEdit=null;kbForm.category='';kbForm.title=''" class="btn btn-secondary text-[11px]"><i class="fa-solid fa-xmark"></i> Annuler</button>
    </div>
  </div>

  <!-- Filtres et liste -->
  <div class="card">
    <div class="flex items-center gap-3 mb-3">
      <input x-model="kbQ" placeholder="Rechercher..." class="bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-[12px] text-slate-200 w-64">
      <select x-model="kbCatF" class="bg-slate-800 border border-slate-700 rounded-lg px-2 py-1.5 text-[12px] text-slate-200">
        <option value="">Toutes categories</option>
        <template x-for="c in kbCatOpts" :key="c"><option :value="c" x-text="c"></option></template>
      </select>
      <span class="text-[11px] text-slate-500 ml-auto" x-text="fKbs.length+' / '+kbs.length+' connaissances'"></span>
    </div>
    <div class="overflow-x-auto max-h-[500px] overflow-y-auto">
      <table class="w-full text-left">
        <thead class="text-[10px] text-slate-500 uppercase border-b border-slate-800 sticky top-0 bg-[#0F172A]">
          <tr><th class="py-2 px-2">Categorie</th><th class="py-2 px-2">Titre</th><th class="py-2 px-2">Pays</th><th class="py-2 px-2">Mots-cles</th><th class="py-2 px-2">Actif</th><th class="py-2 px-2">V.</th><th class="py-2 px-2">Actions</th></tr>
        </thead>
        <tbody>
          <template x-for="k in fKbs" :key="k.id">
          <tr class="border-b border-slate-800/50 hover:bg-slate-800/20">
            <td class="py-2 px-2"><span class="badge b-active text-[9px]" x-text="k.category"></span><br><span class="text-[10px] text-slate-600" x-text="k.subcategory"></span></td>
            <td class="py-2 px-2 text-[12px] text-slate-200 max-w-xs"><span class="font-medium" x-text="k.title"></span><p class="text-[10px] text-slate-500 mt-0.5 line-clamp-2" x-text="(k.content||'').slice(0,120)+'...'"></p></td>
            <td class="py-2 px-2 text-[11px] text-slate-400" x-text="k.country||'-'"></td>
            <td class="py-2 px-2 text-[10px] text-slate-500 max-w-[120px] truncate" x-text="(k.keywords||[]).join(', ')"></td>
            <td class="py-2 px-2"><span class="w-2 h-2 rounded-full inline-block" :class="k.is_active?'bg-emerald-400':'bg-red-400'"></span></td>
            <td class="py-2 px-2 text-[11px] text-slate-500" x-text="k.version||1"></td>
            <td class="py-2 px-2">
              <div class="flex gap-1">
                <button @click="kbEditItem(k)" class="btn btn-secondary text-[10px] py-0.5 px-1.5"><i class="fa-solid fa-pen"></i></button>
                <button @click="kbToggle(k)" class="btn text-[10px] py-0.5 px-1.5" :class="k.is_active?'btn-warning':'btn-success'"><i :class="k.is_active?'fa-solid fa-eye-slash':'fa-solid fa-eye'"></i></button>
                <button @click="kbDelete(k)" class="btn btn-danger text-[10px] py-0.5 px-1.5"><i class="fa-solid fa-trash"></i></button>
              </div>
            </td>
          </tr>
          </template>
        </tbody>
      </table>
      <p x-show="!fKbs.length" class="text-slate-600 text-[12px] italic py-4 text-center">Aucune connaissance trouvee. Cliquez sur "Peupler initiale" ou "Ajouter".</p>
    </div>
  </div>
</section>

<!-- TAB: CODE CENTER -->
<section x-show="tab==='code'" x-cloak x-init="$watch('tab',v=>{if(v==='code'&&!codeFiles.length)codeLoadFiles()})">
  <div class="flex items-center justify-between mb-3">
    <h2 class="text-lg font-semibold text-slate-200"><i class="fa-solid fa-code mr-2 text-emerald-400"></i>Centre de Code <span class="text-[11px] text-slate-500 font-normal ml-2">IA: Gemini Flash + Groq</span></h2>
    <div class="flex gap-2">
      <button @click="gitShowPanel=!gitShowPanel;if(gitShowPanel)gitLoadStatus()" class="btn btn-success text-[11px]"><i class="fa-brands fa-github"></i> Git</button>
      <button @click="codeRestart()" class="btn btn-warning text-[11px]"><i class="fa-solid fa-arrows-rotate"></i> Redemarrer Tendo</button>
      <button @click="codeLoadFiles()" class="btn btn-secondary text-[11px]"><i class="fa-solid fa-folder-open"></i> Rafraichir fichiers</button>
    </div>
  </div>
  <div x-show="codeMsg" class="mb-2 text-[11px] text-emerald-400 bg-emerald-900/20 rounded-lg px-3 py-2 border border-emerald-800/30 flex items-center gap-2" x-cloak><i class="fa-solid fa-circle-check"></i><span x-text="codeMsg"></span></div>

  <!-- Git Panel -->
  <div x-show="gitShowPanel" class="card mb-3" x-cloak>
    <div class="flex items-center justify-between mb-2">
      <div class="card-header mb-0"><i class="fa-brands fa-github"></i> Git — <span class="text-cyan-400 font-mono text-[11px]" x-text="gitStatus.branch||'...'"></span></div>
      <div class="flex gap-2">
        <button @click="gitLoadStatus()" class="btn btn-secondary text-[10px] py-1"><i class="fa-solid fa-arrows-rotate"></i></button>
        <button @click="gitShowPanel=false" class="btn btn-secondary text-[10px] py-1"><i class="fa-solid fa-xmark"></i></button>
      </div>
    </div>
    <div class="grid grid-cols-3 gap-3">
      <!-- Fichiers modifies -->
      <div>
        <p class="text-[10px] text-slate-500 font-semibold mb-1 uppercase">Fichiers modifies</p>
        <div class="bg-[#0B1121] rounded-lg p-2 max-h-32 overflow-y-auto border border-slate-800">
          <p x-show="gitStatus.clean" class="text-[10px] text-slate-600 italic">Aucune modification</p>
          <template x-for="f in (gitStatus.files||[])" :key="f.file">
            <div class="text-[10px] font-mono py-0.5 flex items-center gap-1.5">
              <span :class="f.status==='M'?'text-amber-400':f.status==='A'||f.status==='??'?'text-emerald-400':'text-red-400'" x-text="f.status"></span>
              <span class="text-slate-300 truncate" x-text="f.file"></span>
            </div>
          </template>
        </div>
      </div>
      <!-- Commit -->
      <div>
        <p class="text-[10px] text-slate-500 font-semibold mb-1 uppercase">Nouveau commit</p>
        <input x-model="gitCommitMsg" class="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-[11px] text-slate-200 mb-1.5" placeholder="Message de commit...">
        <div class="flex gap-1.5">
          <button @click="gitCommit()" :disabled="gitBusy||!gitCommitMsg.trim()" class="btn btn-primary text-[10px] py-1 flex-1"><i class="fa-solid fa-check" :class="gitBusy&&'fa-spin'"></i> Commit</button>
          <button @click="gitPush()" :disabled="gitBusy" class="btn btn-success text-[10px] py-1 flex-1"><i class="fa-solid fa-cloud-arrow-up" :class="gitBusy&&'fa-spin'"></i> Push</button>
        </div>
        <div x-show="gitMsg" class="mt-1.5 text-[10px] rounded px-2 py-1 border" :class="gitMsgOk?'text-emerald-400 bg-emerald-900/20 border-emerald-800/30':'text-red-400 bg-red-900/20 border-red-800/30'" x-text="gitMsg" x-cloak></div>
      </div>
      <!-- Derniers commits -->
      <div>
        <p class="text-[10px] text-slate-500 font-semibold mb-1 uppercase">Derniers commits</p>
        <div class="bg-[#0B1121] rounded-lg p-2 max-h-32 overflow-y-auto border border-slate-800">
          <template x-for="(c,i) in (gitStatus.recent_commits||[])" :key="i">
            <div class="text-[10px] font-mono py-0.5 text-slate-400 truncate" x-text="c"></div>
          </template>
        </div>
      </div>
    </div>
  </div>

  <div class="grid grid-cols-12 gap-3" style="height:calc(100vh - 160px)">

    <!-- Colonne gauche : Arborescence fichiers -->
    <div class="col-span-2 card overflow-y-auto" style="max-height:calc(100vh - 160px)">
      <div class="card-header text-[11px]"><i class="fa-solid fa-folder-tree"></i> Fichiers projet</div>
      <div class="text-[11px]" x-data="{openDirs:{}}">
        <template x-for="f in codeFiles" :key="f">
          <div @click="codeOpenFile(f)" class="px-2 py-1 cursor-pointer rounded hover:bg-slate-800/50 truncate flex items-center gap-1.5"
               :class="codeCurFile===f?'bg-cyan-900/30 text-cyan-300':'text-slate-400'">
            <i class="text-[9px]" :class="f.endsWith('.py')?'fa-brands fa-python text-blue-400':f.endsWith('.html')?'fa-solid fa-code text-orange-400':f.endsWith('.json')?'fa-solid fa-brackets-curly text-yellow-400':f.endsWith('.yml')||f.endsWith('.yaml')?'fa-solid fa-gear text-purple-400':'fa-solid fa-file text-slate-600'"></i>
            <span x-text="f.split('/').pop()" :title="f"></span>
          </div>
        </template>
        <p x-show="!codeFiles.length" class="text-slate-600 italic text-[10px] px-2">Chargement...</p>
      </div>
    </div>

    <!-- Colonne centre : Editeur de code -->
    <div class="col-span-5 flex flex-col gap-2" style="max-height:calc(100vh - 160px)">
      <!-- Header editeur -->
      <div class="card py-2 px-3 flex items-center justify-between">
        <div class="flex items-center gap-2">
          <i class="fa-solid fa-file-code text-slate-500 text-[12px]"></i>
          <span class="text-[12px] text-slate-300 font-mono" x-text="codeCurFile||'Aucun fichier ouvert'"></span>
          <span x-show="codeModified" class="badge b-trial text-[8px] ml-1">modifie</span>
        </div>
        <div class="flex gap-1.5">
          <button x-show="codeModified" @click="codeRevert()" class="btn btn-secondary text-[10px] py-1 px-2"><i class="fa-solid fa-rotate-left"></i> Annuler</button>
          <button x-show="codeModified" @click="codeSaveFile()" :disabled="codeSaveBusy" class="btn btn-primary text-[10px] py-1 px-2"><i class="fa-solid fa-save" :class="codeSaveBusy&&'fa-spin'"></i> Sauvegarder</button>
        </div>
      </div>
      <!-- Zone editeur -->
      <div class="card flex-1 p-0 overflow-hidden flex flex-col">
        <div class="flex items-center justify-between px-3 py-1.5 border-b border-slate-800">
          <span class="text-[10px] text-slate-500 font-mono" x-text="codeCurLang"></span>
          <span class="text-[10px] text-slate-600" x-text="codeCurContent?codeCurContent.split('\\n').length+' lignes':''"></span>
        </div>
        <div class="flex-1 overflow-auto relative">
          <div class="flex" style="min-height:100%">
            <!-- Numeros de ligne -->
            <div class="bg-slate-900/50 text-right pr-2 pl-2 pt-2 select-none border-r border-slate-800" style="min-width:40px">
              <template x-for="(l,i) in (codeCurContent||'').split('\n')" :key="i">
                <div class="text-[10px] leading-[18px] text-slate-700 font-mono" x-text="i+1"></div>
              </template>
            </div>
            <!-- Textarea editeur -->
            <textarea x-model="codeCurContent" spellcheck="false"
              class="flex-1 bg-transparent text-[11px] leading-[18px] text-slate-200 font-mono p-2 resize-none outline-none border-none w-full"
              style="tab-size:4;-moz-tab-size:4" :placeholder="codeCurFile?'':'Selectionnez un fichier a gauche...'"></textarea>
          </div>
        </div>
      </div>
      <!-- Terminal / Shell -->
      <div class="card p-2" style="max-height:160px">
        <div class="flex items-center gap-2 mb-1.5">
          <span class="text-[10px] text-slate-500 font-mono"><i class="fa-solid fa-terminal mr-1"></i>Shell</span>
          <div class="flex-1 flex gap-1">
            <input x-model="codeCmdInput" @keydown.enter="codeRunCmd()" class="flex-1 bg-slate-900 border border-slate-700 rounded px-2 py-1 text-[11px] text-emerald-300 font-mono" placeholder="Commande shell (ex: docker logs tendo-api --tail 20)">
            <button @click="codeRunCmd()" :disabled="codeCmdBusy" class="btn btn-secondary text-[10px] py-1 px-2"><i class="fa-solid fa-play" :class="codeCmdBusy&&'fa-spin'"></i></button>
          </div>
        </div>
        <div class="bg-[#0B1121] rounded p-2 text-[10px] font-mono text-slate-400 overflow-auto" style="max-height:100px"><pre x-text="codeCmdOutput||'$ _'"></pre></div>
      </div>
    </div>

    <!-- Colonne droite : Chat IA -->
    <div class="col-span-5 flex flex-col gap-2" style="max-height:calc(100vh - 160px)">
      <div class="card flex-1 flex flex-col overflow-hidden">
        <div class="card-header flex items-center justify-between py-2">
          <div class="flex items-center gap-2"><i class="fa-solid fa-robot text-emerald-400"></i><span class="text-[12px]">Assistant Code IA</span></div>
          <button @click="codeClearChat()" class="btn btn-secondary text-[9px] py-0.5 px-2"><i class="fa-solid fa-eraser"></i> Effacer</button>
        </div>
        <!-- Messages -->
        <div id="codeChatScroll" class="flex-1 overflow-y-auto px-3 py-2 space-y-3">
          <div x-show="!codeChat.length" class="text-center py-8">
            <i class="fa-solid fa-wand-magic-sparkles text-3xl text-slate-700 mb-3"></i>
            <p class="text-[12px] text-slate-500">Decrivez ce que vous voulez faire en langage naturel.</p>
            <p class="text-[10px] text-slate-600 mt-2">Exemples :</p>
            <div class="space-y-1 mt-2">
              <p @click="codePrompt='Ajoute un nouveau scraper pour le site ungm.org'" class="text-[10px] text-cyan-500/70 cursor-pointer hover:text-cyan-400">"Ajoute un nouveau scraper pour ungm.org"</p>
              <p @click="codePrompt='Modifie le fichier config.py pour ajouter un parametre MAX_RETRIES=3'" class="text-[10px] text-cyan-500/70 cursor-pointer hover:text-cyan-400">"Ajoute un parametre MAX_RETRIES=3 dans config.py"</p>
              <p @click="codePrompt='Explique le fonctionnement du scheduler et propose des ameliorations'" class="text-[10px] text-cyan-500/70 cursor-pointer hover:text-cyan-400">"Explique le scheduler et propose des ameliorations"</p>
              <p @click="codePrompt='Corrige les erreurs dans les logs recents'" class="text-[10px] text-cyan-500/70 cursor-pointer hover:text-cyan-400">"Corrige les erreurs dans les logs recents"</p>
            </div>
          </div>
          <template x-for="(msg,i) in codeChat" :key="i">
            <div :class="msg.role==='user'?'flex justify-end':'flex justify-start'">
              <div :class="msg.role==='user'?'bg-cyan-900/30 border-cyan-800/30 max-w-[85%]':'bg-slate-800/50 border-slate-700/30 max-w-[90%]'" class="rounded-lg px-3 py-2 border">
                <div class="flex items-center gap-1.5 mb-1">
                  <i :class="msg.role==='user'?'fa-solid fa-user text-cyan-500':'fa-solid fa-robot text-emerald-400'" class="text-[9px]"></i>
                  <span class="text-[9px] font-semibold" :class="msg.role==='user'?'text-cyan-400':'text-emerald-400'" x-text="msg.role==='user'?'Vous':'IA'"></span>
                </div>
                <div class="text-[11px] text-slate-300 whitespace-pre-wrap" x-text="msg.text"></div>
                <!-- Bloc code genere -->
                <template x-if="msg.code">
                  <div class="mt-2">
                    <div class="flex items-center justify-between bg-slate-900 rounded-t px-2 py-1 border border-slate-700 border-b-0">
                      <span class="text-[9px] text-slate-500 font-mono" x-text="msg.file||'code'"></span>
                      <div class="flex gap-1">
                        <button @click="codeApply(msg)" class="text-[9px] bg-emerald-600 hover:bg-emerald-500 text-white px-2 py-0.5 rounded font-medium"><i class="fa-solid fa-check mr-1"></i>Appliquer</button>
                        <button @click="navigator.clipboard.writeText(msg.code)" class="text-[9px] bg-slate-700 hover:bg-slate-600 text-slate-300 px-2 py-0.5 rounded"><i class="fa-solid fa-copy"></i></button>
                      </div>
                    </div>
                    <pre class="bg-[#0B1121] text-[10px] text-emerald-300 font-mono p-2 rounded-b border border-slate-700 border-t-0 overflow-x-auto max-h-60 overflow-y-auto"><code x-text="msg.code"></code></pre>
                  </div>
                </template>
              </div>
            </div>
          </template>
          <div x-show="codeGenBusy" class="flex justify-start">
            <div class="bg-slate-800/50 border border-slate-700/30 rounded-lg px-3 py-2">
              <div class="flex items-center gap-2"><i class="fa-solid fa-spinner fa-spin text-emerald-400 text-[10px]"></i><span class="text-[11px] text-slate-400">L'IA reflechit...</span></div>
            </div>
          </div>
        </div>
        <!-- Input prompt -->
        <div class="border-t border-slate-800 px-3 py-2">
          <div class="flex gap-2">
            <textarea x-model="codePrompt" @keydown.ctrl.enter="codeGenerate()" rows="2" class="flex-1 bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-[12px] text-slate-200 resize-none" placeholder="Decrivez votre modification en francais... (Ctrl+Entree pour envoyer)"></textarea>
            <button @click="codeGenerate()" :disabled="codeGenBusy||!codePrompt.trim()" class="btn btn-primary self-end text-[11px]"><i class="fa-solid fa-paper-plane" :class="codeGenBusy&&'fa-spin'"></i></button>
          </div>
          <p class="text-[9px] text-slate-600 mt-1">L'IA lit le fichier ouvert et genere du code adapte. Verifiez toujours avant de sauvegarder.</p>
        </div>
      </div>
    </div>
  </div>
</section>

  </div>
</div>

<script>
function dashboard(){return{
  keyIn:'',apiKey:localStorage.getItem('tk')||'',auth:false,errMsg:'',busy:false,lastRefresh:'',
  tab:'dash',
  tabs:[
    {id:'dash',icon:'fa-solid fa-gauge-high',label:'Tableau de Bord'},
    {id:'finance',icon:'fa-solid fa-coins',label:'Finance'},
    {id:'market',icon:'fa-solid fa-file-lines',label:'Marche'},
    {id:'users',icon:'fa-solid fa-users',label:'Utilisateurs'},
    {id:'ops',icon:'fa-solid fa-server',label:'Operations'},
    {id:'knowledge',icon:'fa-solid fa-book',label:'Connaissances'},
    {id:'code',icon:'fa-solid fa-code',label:'Centre de Code'},
  ],
  S:{},users:[],pubs:[],sys:{},logs:[],kbs:[],kbEdit:null,kbForm:{category:'',subcategory:'',title:'',content:'',summary:'',keywords:'',country:'Benin',source_url:'',language:'fr'},kbQ:'',kbCatF:'',kbMsg:'',
  // Code Center state
  codeFiles:[],codeTree:[],codeCurFile:'',codeCurContent:'',codeCurLang:'python',codeOrigContent:'',
  codePrompt:'',codeChat:[],codeGenBusy:false,codeSaveBusy:false,codeMsg:'',
  codeCmdInput:'',codeCmdOutput:'',codeCmdBusy:false,codeShowDiff:false,
  gitShowPanel:false,gitStatus:{},gitCommitMsg:'',gitBusy:false,gitMsg:'',gitMsgOk:true,
  usrQ:'',usrF:'',pubQ:'',pubSrcF:'',pubTypeF:'',pubDetail:null,
  trigging:{},trigMsg:'',autoLog:false,_logI:null,

  async boot(){if(this.apiKey)await this.login(true)},

  async login(silent){
    const k=silent?this.apiKey:this.keyIn;if(!k)return;
    try{const r=await fetch('/admin/api/stats?key='+encodeURIComponent(k));
    if(!r.ok){this.errMsg='Cle invalide';return}
    this.apiKey=k;this.auth=true;this.errMsg='';localStorage.setItem('tk',k);
    await this.refreshAll();
    this._logI=setInterval(()=>{if(this.autoLog&&this.tab==='ops')this.loadLogs()},4000);
    }catch(e){this.errMsg='Connexion impossible: '+e.message}
  },

  async refreshAll(){
    this.busy=true;
    await Promise.all([this.loadStats(),this.loadUsers(),this.loadPubs(),this.loadSys(),this.loadLogs(),this.loadKnowledge()]);
    this.lastRefresh=new Date().toLocaleTimeString('fr-FR');
    this.busy=false;this.$nextTick(()=>{this.drawCharts()});
  },

  async api(p){const r=await fetch('/admin/api'+p+(p.includes('?')?'&':'?')+'key='+encodeURIComponent(this.apiKey));if(!r.ok)throw new Error(r.status);return r.json()},
  async loadStats(){try{this.S=await this.api('/stats')}catch(e){}},
  async loadUsers(){try{const d=await this.api('/users?limit=1000');this.users=d.users??d}catch(e){}},
  async loadPubs(){try{const d=await this.api('/publications?limit=2000');this.pubs=d.publications??d}catch(e){}},
  async loadSys(){try{this.sys=await this.api('/system')}catch(e){}},
  async loadLogs(){try{const d=await this.api('/logs');this.logs=d.logs??[]}catch(e){}},
  async loadKnowledge(){try{const d=await this.api('/knowledge');this.kbs=d.items??[]}catch(e){}},

  get fKbs(){return this.kbs.filter(k=>{if(this.kbCatF&&k.category!==this.kbCatF)return false;if(!this.kbQ)return true;const q=this.kbQ.toLowerCase();return(k.title||'').toLowerCase().includes(q)||(k.content||'').toLowerCase().includes(q)||(k.category||'').toLowerCase().includes(q)})},
  get kbCatOpts(){return[...new Set(this.kbs.map(k=>k.category))].filter(Boolean).sort()},

  kbNew(){this.kbEdit=null;this.kbForm={category:'',subcategory:'',title:'',content:'',summary:'',keywords:'',country:'Benin',source_url:'',language:'fr'};this.kbMsg=''},
  kbEditItem(k){this.kbEdit=k.id;this.kbForm={category:k.category||'',subcategory:k.subcategory||'',title:k.title||'',content:k.content||'',summary:k.summary||'',keywords:(k.keywords||[]).join(', '),country:k.country||'Benin',source_url:k.source_url||'',language:k.language||'fr'};this.kbMsg=''},
  async kbSave(){
    const payload={...this.kbForm,keywords:this.kbForm.keywords?this.kbForm.keywords.split(',').map(s=>s.trim()).filter(Boolean):[]};
    try{
      if(this.kbEdit){
        const r=await fetch('/admin/api/knowledge/'+this.kbEdit+'?key='+encodeURIComponent(this.apiKey),{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
        if(!r.ok)throw new Error('Erreur '+r.status);this.kbMsg='Connaissance mise a jour';
      }else{
        const r=await fetch('/admin/api/knowledge?key='+encodeURIComponent(this.apiKey),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
        if(!r.ok)throw new Error('Erreur '+r.status);this.kbMsg='Connaissance ajoutee';
      }
      await this.loadKnowledge();this.kbEdit=null;this.kbForm={category:'',subcategory:'',title:'',content:'',summary:'',keywords:'',country:'Benin',source_url:'',language:'fr'};
      setTimeout(()=>this.kbMsg='',4000);
    }catch(e){this.kbMsg='Erreur: '+e.message}
  },
  async kbDelete(k){if(!confirm('Supprimer "'+k.title.slice(0,50)+'" ?'))return;try{const r=await fetch('/admin/api/knowledge/'+k.id+'?key='+encodeURIComponent(this.apiKey),{method:'DELETE'});if(r.ok){this.kbs=this.kbs.filter(x=>x.id!==k.id);this.kbMsg='Supprime'}}catch(e){this.kbMsg='Erreur: '+e.message}},
  async kbToggle(k){try{const r=await fetch('/admin/api/knowledge/'+k.id+'?key='+encodeURIComponent(this.apiKey),{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({is_active:!k.is_active})});if(r.ok){k.is_active=!k.is_active}}catch(e){alert(e.message)}},
  async kbSeed(){if(!confirm('Peupler la base avec les connaissances initiales ?'))return;try{const r=await fetch('/admin/trigger/seed-knowledge?key='+encodeURIComponent(this.apiKey),{method:'POST'});const d=await r.json();this.kbMsg=d.message||'OK';await this.loadKnowledge();setTimeout(()=>this.kbMsg='',4000)}catch(e){this.kbMsg='Erreur: '+e.message}},
  async kbLearn(){try{const r=await fetch('/admin/trigger/self-learn?key='+encodeURIComponent(this.apiKey),{method:'POST'});const d=await r.json();this.kbMsg=d.message||'OK';setTimeout(()=>this.kbMsg='',4000)}catch(e){this.kbMsg='Erreur: '+e.message}},

  // ── Code Center ──
  async codeLoadFiles(){
    try{const d=await this.api('/code/files');this.codeFiles=d.files||[];this.codeTree=this._buildTree(this.codeFiles)}catch(e){this.codeMsg='Erreur: '+e.message}
  },
  _buildTree(files){
    const tree={};
    files.forEach(f=>{
      const parts=f.split('/');let node=tree;
      parts.forEach((p,i)=>{
        if(i===parts.length-1){if(!node._files)node._files=[];node._files.push(p)}
        else{if(!node[p])node[p]={};node=node[p]}
      });
    });
    return tree;
  },
  async codeOpenFile(path){
    this.codeCurFile=path;this.codeCurLang=this._detectLang(path);
    try{const d=await this.api('/code/read?path='+encodeURIComponent(path));this.codeCurContent=d.content||'';this.codeOrigContent=d.content||'';this.codeShowDiff=false}catch(e){this.codeMsg='Erreur lecture: '+e.message}
  },
  _detectLang(p){const ext=p.split('.').pop();const m={py:'python',js:'javascript',ts:'typescript',html:'html',css:'css',json:'json',yml:'yaml',yaml:'yaml',md:'markdown',sh:'bash',sql:'sql',txt:'text'};return m[ext]||'text'},
  get codeModified(){return this.codeCurContent!==this.codeOrigContent},
  async codeSaveFile(){
    if(!this.codeCurFile||!this.codeModified)return;
    this.codeSaveBusy=true;
    try{
      const r=await fetch('/admin/api/code/write?key='+encodeURIComponent(this.apiKey),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:this.codeCurFile,content:this.codeCurContent})});
      const d=await r.json();if(!r.ok)throw new Error(d.detail||'Erreur');
      this.codeOrigContent=this.codeCurContent;this.codeMsg='Fichier sauvegarde';this.codeShowDiff=false;setTimeout(()=>this.codeMsg='',3000);
    }catch(e){this.codeMsg='Erreur: '+e.message}finally{this.codeSaveBusy=false}
  },
  async codeRevert(){if(confirm('Annuler les modifications ?')){this.codeCurContent=this.codeOrigContent;this.codeShowDiff=false}},
  async codeGenerate(){
    if(!this.codePrompt.trim())return;
    const userMsg=this.codePrompt.trim();
    this.codeChat.push({role:'user',text:userMsg});this.codePrompt='';this.codeGenBusy=true;
    try{
      const payload={prompt:userMsg,current_file:this.codeCurFile||'',current_content:this.codeCurContent||''};
      const r=await fetch('/admin/api/code/generate?key='+encodeURIComponent(this.apiKey),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      const d=await r.json();if(!r.ok)throw new Error(d.detail||'Erreur IA');
      this.codeChat.push({role:'assistant',text:d.response||'',code:d.code||'',file:d.target_file||'',action:d.action||''});
      // Si l'IA propose du code et un fichier cible, proposer de l'appliquer
      if(d.code && d.target_file){this.codeMsg='Code genere. Cliquez "Appliquer" pour integrer.'}
    }catch(e){this.codeChat.push({role:'assistant',text:'Erreur: '+e.message,code:'',file:'',action:'error'})}finally{this.codeGenBusy=false;this.$nextTick(()=>{const el=document.getElementById('codeChatScroll');if(el)el.scrollTop=el.scrollHeight})}
  },
  async codeApply(msg){
    if(!msg.code)return;
    const targetFile=msg.file||this.codeCurFile;
    if(targetFile&&targetFile!==this.codeCurFile){await this.codeOpenFile(targetFile)}
    if(msg.action==='replace'){this.codeCurContent=msg.code}
    else if(msg.action==='append'){this.codeCurContent+='\n'+msg.code}
    else if(msg.action==='insert'&&msg.code){this.codeCurContent=msg.code}
    else{this.codeCurContent=msg.code}
    this.codeMsg='Code applique dans l\'editeur. Verifiez puis sauvegardez.';setTimeout(()=>this.codeMsg='',4000);
  },
  async codeRunCmd(){
    if(!this.codeCmdInput.trim())return;this.codeCmdBusy=true;this.codeCmdOutput='Execution en cours...';
    try{
      const r=await fetch('/admin/api/code/exec?key='+encodeURIComponent(this.apiKey),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({command:this.codeCmdInput})});
      const d=await r.json();if(!r.ok)throw new Error(d.detail||'Erreur');
      this.codeCmdOutput=d.output||'(aucune sortie)';
    }catch(e){this.codeCmdOutput='ERREUR: '+e.message}finally{this.codeCmdBusy=false}
  },
  async codeRestart(){
    if(!confirm('Redemarrer le conteneur Tendo ?'))return;
    this.codeCmdBusy=true;this.codeCmdOutput='Redemarrage en cours...';
    try{
      const r=await fetch('/admin/api/code/restart?key='+encodeURIComponent(this.apiKey),{method:'POST'});
      const d=await r.json();this.codeCmdOutput=d.message||'Relance en cours...';
    }catch(e){this.codeCmdOutput='ERREUR: '+e.message}finally{this.codeCmdBusy=false}
  },
  codeClearChat(){this.codeChat=[];this.codeMsg=''},

  // ── Git ──
  async gitLoadStatus(){try{const d=await this.api('/code/git/status');this.gitStatus=d}catch(e){this.gitMsg='Erreur: '+e.message;this.gitMsgOk=false}},
  async gitCommit(){
    if(!this.gitCommitMsg.trim())return;this.gitBusy=true;this.gitMsg='';
    try{
      const r=await fetch('/admin/api/code/git/commit?key='+encodeURIComponent(this.apiKey),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:this.gitCommitMsg})});
      const d=await r.json();
      if(d.success){this.gitMsg='Commit OK';this.gitMsgOk=true;this.gitCommitMsg='';await this.gitLoadStatus()}
      else{this.gitMsg=d.output||'Erreur commit';this.gitMsgOk=false}
    }catch(e){this.gitMsg='Erreur: '+e.message;this.gitMsgOk=false}finally{this.gitBusy=false;setTimeout(()=>this.gitMsg='',6000)}
  },
  async gitPush(){
    this.gitBusy=true;this.gitMsg='Push en cours...';this.gitMsgOk=true;
    try{
      const r=await fetch('/admin/api/code/git/push?key='+encodeURIComponent(this.apiKey),{method:'POST'});
      const d=await r.json();
      if(d.success){this.gitMsg='Push OK vers GitHub';this.gitMsgOk=true;await this.gitLoadStatus()}
      else{this.gitMsg=d.output||'Erreur push';this.gitMsgOk=false}
    }catch(e){this.gitMsg='Erreur: '+e.message;this.gitMsgOk=false}finally{this.gitBusy=false;setTimeout(()=>this.gitMsg='',8000)}
  },

  get alerts(){
    const a=[],S=this.S;
    if(S.unclassified_pubs>20)a.push({level:'warning',icon:'fa-solid fa-triangle-exclamation',html:'<b>'+S.unclassified_pubs+'</b> publications sans type de document. Lancez le pipeline PDF/IA pour les classifier.'});
    const exp=this.trialUsers.filter(u=>u.days_left<=3);
    if(exp.length)a.push({level:'danger',icon:'fa-solid fa-clock',html:'<b>'+exp.length+'</b> essai(s) expirent dans les 3 prochains jours. Opportunite de conversion.'});
    const nop=this.users.filter(u=>!u.name||!u.company);
    if(nop.length>3)a.push({level:'info',icon:'fa-solid fa-circle-info',html:'<b>'+nop.length+'</b> utilisateurs sans profil complet (nom ou entreprise manquant).'});
    return a;
  },

  get trialUsers(){
    const now=Date.now();
    return this.users.filter(u=>u.subscription_status==='trial'&&u.trial_end)
      .map(u=>({...u,days_left:Math.max(0,Math.ceil((new Date(u.trial_end)-now)/86400000))}))
      .sort((a,b)=>a.days_left-b.days_left);
  },

  get funnel(){
    const t=this.S.total_users||1,prof=this.users.filter(u=>u.name&&u.company).length,
    active=(this.S.active_essentiel||0)+(this.S.active_premium||0)+(this.S.trial_users||0),
    paid=(this.S.active_essentiel||0)+(this.S.active_premium||0);
    return[{label:'Inscrits',count:this.S.total_users||0,pct:100},{label:'Profil complete',count:prof,pct:Math.round(prof/t*100)},{label:'Essai / Payant',count:active,pct:Math.round(active/t*100)},{label:'Payants',count:paid,pct:Math.round(paid/t*100)}];
  },

  get profileMetrics(){
    const us=this.users,t=us.length||1;
    return[{label:'Avec nom',pct:Math.round(us.filter(u=>u.name).length/t*100)},{label:'Avec entreprise',pct:Math.round(us.filter(u=>u.company).length/t*100)},{label:'Avec email',pct:Math.round(us.filter(u=>u.email_address).length/t*100)},{label:'Avec secteurs',pct:Math.round(us.filter(u=>u.sectors&&u.sectors.length).length/t*100)}];
  },

  get sectorCoverage(){
    const m={};this.pubs.forEach(p=>{(p.sectors||[]).forEach(s=>{m[s]=(m[s]||0)+1})});
    return Object.entries(m).sort((a,b)=>b[1]-a[1]).slice(0,10).map(([s,c])=>({sector:s,count:c,pct:Math.round(c/(this.S.total_pubs||1)*100)}));
  },

  get sectorGaps(){
    const dem={},sup={};
    this.users.forEach(u=>{(u.sectors||[]).forEach(s=>{dem[s]=(dem[s]||0)+1})});
    this.pubs.forEach(p=>{(p.sectors||[]).forEach(s=>{sup[s]=(sup[s]||0)+1})});
    return[...new Set([...Object.keys(dem),...Object.keys(sup)])].map(s=>{
      const d=dem[s]||0,su=sup[s]||0,r=d>0?su/d:999;
      return{sector:s,demand:d,supply:su,gap:r>=3?'OK':r>=1?'Faible':'Insuffisant'};
    }).sort((a,b)=>({Insuffisant:0,Faible:1,OK:2}[a.gap]??3)-({Insuffisant:0,Faible:1,OK:2}[b.gap]??3));
  },

  get fUsers(){return this.users.filter(u=>{if(this.usrF&&u.subscription_status!==this.usrF)return false;if(!this.usrQ)return true;const q=this.usrQ.toLowerCase();return(u.name||'').toLowerCase().includes(q)||(u.phone_number||'').includes(q)||(u.company||'').toLowerCase().includes(q)})},
  get fPubs(){return this.pubs.filter(p=>{if(this.pubSrcF&&p.source!==this.pubSrcF)return false;if(this.pubTypeF&&p.document_type!==this.pubTypeF)return false;if(!this.pubQ)return true;const q=this.pubQ.toLowerCase();return(p.title||'').toLowerCase().includes(q)||(p.reference||'').toLowerCase().includes(q)})},
  get pubSrcOpts(){return[...new Set(this.pubs.map(p=>p.source))].filter(Boolean).sort()},
  get pubTypeOpts(){return[...new Set(this.pubs.map(p=>p.document_type))].filter(Boolean).sort()},

  async toggleUsr(u){try{const r=await fetch('/admin/api/users/'+u.id+'/toggle?key='+encodeURIComponent(this.apiKey),{method:'PATCH'});if(r.ok){const d=await r.json();u.is_active=d.is_active}}catch(e){alert(e.message)}},
  async delPub(p){if(!confirm('Supprimer "'+p.title.slice(0,50)+'…" ?'))return;try{const r=await fetch('/admin/api/publications/'+p.id+'?key='+encodeURIComponent(this.apiKey),{method:'DELETE'});if(r.ok)this.pubs=this.pubs.filter(x=>x.id!==p.id)}catch(e){alert(e.message)}},
  async openPubDetail(p){try{const r=await fetch('/admin/api/publications/'+p.id+'/detail?key='+encodeURIComponent(this.apiKey));if(r.ok){this.pubDetail=await r.json()}else{this.pubDetail=p}}catch(e){this.pubDetail=p}},
  async classifyPub(p){try{const r=await fetch('/admin/api/publications/'+p.id+'/classify?key='+encodeURIComponent(this.apiKey),{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({document_type:p._newType})});if(r.ok){p.document_type=p._newType;p._editing=false}}catch(e){alert(e.message)}},
  async trig(action){this.trigging={...this.trigging,[action]:true};try{const r=await fetch('/admin/trigger/'+action+'?key='+encodeURIComponent(this.apiKey),{method:'POST'});const d=await r.json();this.trigMsg=d.message||'Lance';setTimeout(()=>this.trigMsg='',5000)}catch(e){this.trigMsg='Erreur: '+e.message}finally{this.trigging={...this.trigging,[action]:false}}},

  _charts:{},
  drawCharts(){
    const ud=this.S.users_per_day||{};this._mk('chartUsers','bar',Object.keys(ud),Object.values(ud),'Inscriptions','#0EA5E9');
    const ss=(this.S.publications_by_source||[]);this._mk('chartSources','doughnut',ss.map(s=>s.source),ss.map(s=>s.count),'Pubs',['#0EA5E9','#8B5CF6','#10B981','#F59E0B','#EF4444','#06B6D4','#F97316','#EC4899']);
  },
  _mk(id,type,labels,data,label,colors){
    const el=document.getElementById(id);if(!el)return;if(this._charts[id])this._charts[id].destroy();
    const isD=type==='doughnut';
    this._charts[id]=new Chart(el,{type,data:{labels,datasets:[{label,data,backgroundColor:isD?colors:(colors+'20'),borderColor:isD?'#0F172A':colors,borderWidth:isD?2:2,borderRadius:isD?0:4}]},
    options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:isD,position:'right',labels:{color:'#64748B',font:{size:10,family:'Inter'},padding:12,boxWidth:10}}},
    scales:isD?{}:{x:{ticks:{color:'#475569',font:{size:9,family:'Inter'}},grid:{color:'#1E293B40'}},y:{ticks:{color:'#475569'},grid:{color:'#1E293B'},beginAtZero:true}}}});
  },

  bc(s){return s==='trial'?'b-trial':s==='active'?'b-active':'b-expired'},
  xof(v){if(v==null||v==='')return'-';return new Intl.NumberFormat('fr-FR').format(v)+' F'},
  fdate(s){if(!s)return'-';try{return new Date(s).toLocaleDateString('fr-FR',{day:'2-digit',month:'short',year:'numeric'})}catch{return s}},
}}
</script>
</body>
</html>"""


# ══════════════════════════════════════════════════════════════════════════════
# API JSON
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/api/stats")
async def api_stats(key: str = "", db: AsyncSession = Depends(get_db)):
    _ck(key)

    from app.services.payment import PLANS

    price_ess = PLANS.get("essentiel", {}).get("amount", 0)
    price_prem = PLANS.get("premium", {}).get("amount", 0)

    # ── Utilisateurs ──
    total_users = (await db.execute(select(func.count(User.id)))).scalar() or 0
    trial_users = (await db.execute(select(func.count(User.id)).where(
        User.subscription_status == SubscriptionStatus.TRIAL.value
    ))).scalar() or 0
    expired_users = (await db.execute(select(func.count(User.id)).where(
        User.subscription_status == SubscriptionStatus.EXPIRED.value
    ))).scalar() or 0
    active_ess = (await db.execute(select(func.count(User.id)).where(and_(
        User.subscription_status == SubscriptionStatus.ACTIVE.value,
        User.subscription_plan == "essentiel",
    )))).scalar() or 0
    active_prem = (await db.execute(select(func.count(User.id)).where(and_(
        User.subscription_status == SubscriptionStatus.ACTIVE.value,
        User.subscription_plan == "premium",
    )))).scalar() or 0
    paid_users = active_ess + active_prem

    # ── Publications ──
    total_pubs = (await db.execute(select(func.count(Publication.id)))).scalar() or 0
    pubs_w_pdf = (await db.execute(select(func.count(Publication.id)).where(
        and_(Publication.pdf_url.is_not(None), Publication.pdf_url != "")
    ))).scalar() or 0
    unclassified = (await db.execute(select(func.count(Publication.id)).where(
        or_(Publication.document_type.is_(None), Publication.document_type == "")
    ))).scalar() or 0

    # Par source
    src_res = await db.execute(
        select(Publication.source, func.count(Publication.id).label("cnt"))
        .group_by(Publication.source).order_by(func.count(Publication.id).desc())
    )
    pubs_by_source = [{"source": r.source, "count": r.cnt} for r in src_res]

    # Par type
    type_res = await db.execute(
        select(Publication.document_type, func.count(Publication.id).label("cnt"))
        .group_by(Publication.document_type).order_by(func.count(Publication.id).desc())
    )
    pubs_by_type = [{"type": r.document_type or "Non classifié", "count": r.cnt} for r in type_res]

    # ── Finance ──
    revenue = float((await db.execute(
        select(func.coalesce(func.sum(Subscription.amount), 0)).where(Subscription.status == "paid")
    )).scalar() or 0)
    paid_count = (await db.execute(
        select(func.count(Subscription.id)).where(Subscription.status == "paid")
    )).scalar() or 0
    mrr = (active_ess * price_ess) + (active_prem * price_prem)
    arr = mrr * 12
    arpu = (revenue / paid_count) if paid_count > 0 else 0
    conv = (paid_users / total_users * 100) if total_users > 0 else 0

    # ── Notifications ──
    total_notifs = (await db.execute(select(func.count(Notification.id)))).scalar() or 0

    # ── Récents ──
    recent_u = await db.execute(select(User).order_by(User.created_at.desc()).limit(12))
    recent_users = [
        {"id": u.id, "name": u.name, "phone_number": u.phone_number,
         "company": u.company, "subscription_status": u.subscription_status,
         "subscription_plan": u.subscription_plan,
         "created_at": u.created_at.isoformat() if u.created_at else None}
        for u in recent_u.scalars().all()
    ]

    # ── Paiements ──
    pay_res = await db.execute(
        select(Subscription, User.phone_number)
        .join(User, User.id == Subscription.user_id, isouter=True)
        .order_by(Subscription.start_date.desc()).limit(300)
    )
    all_payments = []
    for sub, phone in pay_res:
        all_payments.append({
            "id": sub.id, "phone": phone, "plan": sub.plan,
            "amount": float(sub.amount or 0), "status": sub.status,
            "transaction_id": sub.payment_id,
            "start_date": sub.start_date.isoformat() if sub.start_date else None,
            "end_date": sub.end_date.isoformat() if sub.end_date else None,
            "created_at": sub.start_date.isoformat() if sub.start_date else None,
        })

    # ── Inscriptions par jour ──
    uday_res = await db.execute(select(User.created_at))
    users_per_day: dict = {}
    for (d,) in uday_res:
        if d:
            k = d.strftime("%Y-%m-%d")
            users_per_day[k] = users_per_day.get(k, 0) + 1

    return {
        "total_users": total_users,
        "trial_users": trial_users,
        "expired_users": expired_users,
        "active_essentiel": active_ess,
        "active_premium": active_prem,
        "paid_users": paid_users,
        "active_users_pct": round((paid_users + trial_users) / total_users * 100) if total_users else 0,
        "total_pubs": total_pubs,
        "pubs_with_pdf": pubs_w_pdf,
        "unclassified_pubs": unclassified,
        "sources_count": len(pubs_by_source),
        "publications_by_source": pubs_by_source,
        "pubs_by_type": pubs_by_type,
        "revenue_total": revenue,
        "paid_subs_count": paid_count,
        "mrr": mrr, "arr": arr,
        "arpu": round(arpu, 2),
        "conversion_rate": round(conv, 2),
        "price_essentiel": price_ess,
        "price_premium": price_prem,
        "total_notifs": total_notifs,
        "recent_users": recent_users,
        "recent_payments": all_payments[:10],
        "all_payments": all_payments,
        "users_per_day": users_per_day,
    }


@router.get("/api/users")
async def api_users(limit: int = Query(1000, le=5000), key: str = "", db: AsyncSession = Depends(get_db)):
    _ck(key)
    res = await db.execute(select(User).order_by(User.created_at.desc()).limit(limit))
    return {"users": [
        {"id": u.id, "phone_number": u.phone_number, "name": u.name,
         "company": u.company, "email_address": u.email_address,
         "subscription_status": u.subscription_status,
         "subscription_plan": u.subscription_plan,
         "is_active": u.is_active, "has_used_trial": getattr(u, 'has_used_trial', True),
         "registration_ip": getattr(u, 'registration_ip', None),
         "sectors": u.sectors, "regions": u.regions,
         "created_at": u.created_at.isoformat() if u.created_at else None,
         "trial_end": u.trial_end.isoformat() if u.trial_end else None}
        for u in res.scalars().all()
    ]}


@router.get("/api/publications")
async def api_publications(limit: int = Query(2000, le=10000), key: str = "", db: AsyncSession = Depends(get_db)):
    _ck(key)
    res = await db.execute(select(Publication).order_by(Publication.created_at.desc()).limit(limit))
    return {"publications": [
        {"id": p.id, "source": p.source, "reference": p.reference,
         "title": p.title, "document_type": p.document_type,
         "budget": p.budget, "sectors": p.sectors, "regions": p.regions,
         "authority_name": p.authority_name, "financing_source": p.financing_source,
         "country": p.country,
         "deadline": p.deadline.isoformat() if p.deadline else None,
         "created_at": p.created_at.isoformat() if p.created_at else None,
         "pdf_url": p.pdf_url, "is_processed": p.is_processed}
        for p in res.scalars().all()
    ]}


@router.get("/api/publications/{pub_id}/detail")
async def api_publication_detail(pub_id: int, key: str = "", db: AsyncSession = Depends(get_db)):
    _ck(key)
    res = await db.execute(select(Publication).where(Publication.id == pub_id))
    pub = res.scalar_one_or_none()
    if not pub:
        raise HTTPException(status_code=404, detail="Publication non trouvée")
    return {
        "id": pub.id, "source": pub.source, "reference": pub.reference,
        "title": pub.title, "document_type": pub.document_type,
        "budget": pub.budget, "sectors": pub.sectors, "regions": pub.regions,
        "authority_name": pub.authority_name, "authority_email": pub.authority_email,
        "financing_source": pub.financing_source, "country": pub.country,
        "deadline": pub.deadline.isoformat() if pub.deadline else None,
        "created_at": pub.created_at.isoformat() if pub.created_at else None,
        "pdf_url": pub.pdf_url, "is_processed": pub.is_processed,
        "technical_summary": pub.technical_summary,
        "required_documents": pub.required_documents,
        "qualification_criteria": pub.qualification_criteria,
        "guarantee_amount": pub.guarantee_amount,
        "html_content": pub.html_content[:5000] if pub.html_content else None,
    }


class ClassifyRequest(BaseModel):
    document_type: str = ""


@router.patch("/api/publications/{pub_id}/classify")
async def api_classify_publication(pub_id: int, body: ClassifyRequest, key: str = "", db: AsyncSession = Depends(get_db)):
    _ck(key)
    res = await db.execute(select(Publication).where(Publication.id == pub_id))
    pub = res.scalar_one_or_none()
    if not pub:
        raise HTTPException(status_code=404, detail="Publication non trouvée")
    pub.document_type = body.document_type or None
    await db.commit()
    return {"id": pub.id, "document_type": pub.document_type}


@router.get("/api/system")
async def api_system(key: str = ""):
    _ck(key)
    from app.scheduler import scheduler

    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            "id": job.id, "name": job.name,
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
        })

    return {
        "scheduler_running": scheduler.running,
        "jobs": jobs,
        "env": {
            "ENVIRONMENT": getattr(settings, "environment", "production"),
            "DATABASE": "PostgreSQL (async)",
            "SCHEDULER": "APScheduler (in-process)",
            "WHATSAPP": "Meta Cloud API v21",
            "LLM": "Groq → Gemini Flash → Claude",
            "FEDAPAY": "configuré" if getattr(settings, "fedapay_secret_key", "") else "non configuré",
            "SCRAPERS": "ARMP, JNMP, BAD, ADPME, gouv.bj, marches-publics.bj, BM",
        },
    }


@router.get("/api/logs")
async def api_logs(key: str = ""):
    _ck(key)
    return {"logs": list(reversed(list(_log_buffer)[-200:]))}


@router.patch("/api/users/{user_id}/toggle")
async def api_toggle_user(user_id: int, key: str = "", db: AsyncSession = Depends(get_db)):
    _ck(key)
    res = await db.execute(select(User).where(User.id == user_id))
    user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur non trouvé")
    user.is_active = not user.is_active
    await db.commit()
    return {"id": user.id, "is_active": user.is_active}


@router.delete("/api/publications/{pub_id}")
async def api_delete_pub(pub_id: int, key: str = "", db: AsyncSession = Depends(get_db)):
    _ck(key)
    await db.execute(delete(Notification).where(Notification.publication_id == pub_id))
    res = await db.execute(select(Publication).where(Publication.id == pub_id))
    pub = res.scalar_one_or_none()
    if not pub:
        raise HTTPException(status_code=404, detail="Publication non trouvée")
    await db.delete(pub)
    await db.commit()
    return {"message": f"Publication {pub_id} supprimée"}


# ── Déclencheurs manuels ──────────────────────────────────────────────────────

@router.post("/trigger/scraping")
async def trigger_scraping(key: str = ""):
    _ck(key)
    from app.scheduler import job_run_scrapers
    asyncio.create_task(job_run_scrapers())
    logger.info("[Admin] Scraping déclenché manuellement")
    return {"status": "started", "message": "Scraping lancé en arrière-plan"}


@router.post("/trigger/notifications")
async def trigger_notifications(key: str = ""):
    _ck(key)
    from app.scheduler import job_send_notifications
    asyncio.create_task(job_send_notifications())
    logger.info("[Admin] Notifications déclenchées manuellement")
    return {"status": "started", "message": "Envoi des notifications lancé"}


@router.post("/trigger/jnmp")
async def trigger_jnmp(key: str = ""):
    _ck(key)
    from app.scheduler import job_process_jnmp_journals
    asyncio.create_task(job_process_jnmp_journals())
    logger.info("[Admin] Segmentation JNMP déclenchée manuellement")
    return {"status": "started", "message": "Analyse journaux JNMP lancée"}


@router.post("/trigger/pdf-processing")
async def trigger_pdf(key: str = ""):
    _ck(key)
    async def _run():
        try:
            from app.scheduler import job_enrich_publications
            await job_enrich_publications()
            logger.info("[Admin] Pipeline PDF/IA terminé")
        except Exception as e:
            logger.error(f"[Admin] Erreur pipeline PDF: {e}")
    asyncio.create_task(_run())
    return {"status": "started", "message": "Pipeline PDF/IA lancé (enrichissement DeepSeek)"}


@router.post("/trigger/cleanup-expired")
async def trigger_cleanup(key: str = ""):
    _ck(key)
    async def _run():
        from app.scheduler import job_cleanup_expired_publications
        await job_cleanup_expired_publications()
    asyncio.create_task(_run())
    return {"status": "started", "message": "Nettoyage AO expirés lancé"}


@router.post("/trigger/enrich-publications")
async def trigger_enrich(key: str = ""):
    _ck(key)
    async def _run():
        from app.scheduler import job_enrich_publications
        await job_enrich_publications()
    asyncio.create_task(_run())
    return {"status": "started", "message": "Enrichissement IA lancé"}


@router.post("/trigger/proactive-discussion")
async def trigger_proactive(key: str = ""):
    _ck(key)
    async def _run():
        from app.scheduler import job_proactive_discussions
        await job_proactive_discussions()
    asyncio.create_task(_run())
    return {"status": "started", "message": "Discussion proactive lancée"}


@router.post("/trigger/seed-knowledge")
async def trigger_seed_knowledge(key: str = ""):
    _ck(key)
    async def _run():
        from app.utils.db import AsyncSessionLocal
        from app.services.knowledge_service import seed_initial_knowledge
        async with AsyncSessionLocal() as db:
            await seed_initial_knowledge(db)
    asyncio.create_task(_run())
    return {"status": "started", "message": "Peuplement base de connaissances lance"}


@router.post("/trigger/self-learn")
async def trigger_self_learn(key: str = ""):
    _ck(key)
    async def _run():
        from app.scheduler import job_self_learn
        await job_self_learn()
    asyncio.create_task(_run())
    return {"status": "started", "message": "Auto-apprentissage lance en arriere-plan"}


# ══════════════════════════════════════════════════════════════════════════════
# API Knowledge Base CRUD
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/api/knowledge")
async def api_knowledge(key: str = "", db: AsyncSession = Depends(get_db)):
    _ck(key)
    res = await db.execute(
        select(KnowledgeBase).order_by(KnowledgeBase.category, KnowledgeBase.title)
    )
    items = []
    for kb in res.scalars().all():
        items.append({
            "id": kb.id, "category": kb.category, "subcategory": kb.subcategory,
            "title": kb.title, "content": kb.content, "summary": kb.summary,
            "keywords": kb.keywords or [], "country": kb.country,
            "source_url": kb.source_url, "language": kb.language,
            "is_active": kb.is_active, "version": kb.version,
            "created_at": kb.created_at.isoformat() if kb.created_at else None,
            "updated_at": kb.updated_at.isoformat() if kb.updated_at else None,
        })
    return {"items": items, "total": len(items)}


@router.post("/api/knowledge")
async def api_create_knowledge(data: KnowledgeCreate, key: str = "", db: AsyncSession = Depends(get_db)):
    _ck(key)
    kb = KnowledgeBase(
        category=data.category,
        subcategory=data.subcategory,
        title=data.title,
        content=data.content,
        summary=data.summary,
        keywords=data.keywords or [],
        country=data.country,
        source_url=data.source_url,
        language=data.language,
    )
    db.add(kb)
    await db.commit()
    await db.refresh(kb)
    logger.info(f"[Admin] Connaissance ajoutee: {kb.title}")
    return {"id": kb.id, "message": "Connaissance creee"}


@router.put("/api/knowledge/{kb_id}")
async def api_update_knowledge(kb_id: int, data: KnowledgeUpdate, key: str = "", db: AsyncSession = Depends(get_db)):
    _ck(key)
    res = await db.execute(select(KnowledgeBase).where(KnowledgeBase.id == kb_id))
    kb = res.scalar_one_or_none()
    if not kb:
        raise HTTPException(status_code=404, detail="Connaissance non trouvee")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(kb, field, value)
    kb.version = (kb.version or 1) + 1
    await db.commit()
    logger.info(f"[Admin] Connaissance mise a jour: {kb.title}")
    return {"id": kb.id, "message": "Connaissance mise a jour"}


@router.delete("/api/knowledge/{kb_id}")
async def api_delete_knowledge(kb_id: int, key: str = "", db: AsyncSession = Depends(get_db)):
    _ck(key)
    res = await db.execute(select(KnowledgeBase).where(KnowledgeBase.id == kb_id))
    kb = res.scalar_one_or_none()
    if not kb:
        raise HTTPException(status_code=404, detail="Connaissance non trouvee")
    await db.delete(kb)
    await db.commit()
    logger.info(f"[Admin] Connaissance supprimee: {kb.title}")
    return {"message": f"Connaissance {kb_id} supprimee"}


# ═���═══════════════════════════��════════════════════════════════════════════════
# API Code Center
# ═════════════════════════���════════════════════════════════════════════════════

import os
import subprocess

# Repertoire racine du projet (dans le conteneur Docker = /app, sur host = /opt/tendo)
_PROJECT_ROOT = os.environ.get("PROJECT_ROOT", "/app")
# Extensions autorisees pour lecture/ecriture
_ALLOWED_EXTS = {".py", ".html", ".css", ".js", ".json", ".yml", ".yaml", ".toml", ".cfg",
                 ".txt", ".md", ".sh", ".sql", ".env.example", ".dockerfile", ""}
# Dossiers a exclure du listing
_EXCLUDED_DIRS = {"__pycache__", ".git", "node_modules", ".venv", "venv", ".mypy_cache",
                  ".pytest_cache", "htmlcov", ".ruff_cache", "eggs", "*.egg-info"}


def _safe_path(path: str) -> str:
    """Valide et retourne le chemin absolu securise dans le projet."""
    # Nettoyer : pas de .. ni chemins absolus
    clean = path.replace("\\", "/").lstrip("/")
    if ".." in clean:
        raise HTTPException(status_code=400, detail="Chemin interdit")
    full = os.path.join(_PROJECT_ROOT, clean)
    # Verifier que le chemin reste dans le projet
    if not os.path.realpath(full).startswith(os.path.realpath(_PROJECT_ROOT)):
        raise HTTPException(status_code=400, detail="Chemin hors projet")
    return full


@router.get("/api/code/files")
async def api_code_files(key: str = ""):
    """Liste les fichiers du projet."""
    _ck(key)
    files = []
    for root, dirs, filenames in os.walk(_PROJECT_ROOT):
        # Exclure les dossiers non pertinents
        dirs[:] = [d for d in dirs if d not in _EXCLUDED_DIRS and not d.endswith(".egg-info")]
        rel_root = os.path.relpath(root, _PROJECT_ROOT).replace("\\", "/")
        if rel_root == ".":
            rel_root = ""
        for fn in sorted(filenames):
            ext = os.path.splitext(fn)[1].lower()
            if ext in _ALLOWED_EXTS or fn in ("Dockerfile", "Makefile", ".env.example"):
                rel_path = f"{rel_root}/{fn}" if rel_root else fn
                files.append(rel_path)
    return {"files": sorted(files)}


@router.get("/api/code/read")
async def api_code_read(path: str, key: str = ""):
    """Lit le contenu d'un fichier."""
    _ck(key)
    full = _safe_path(path)
    if not os.path.isfile(full):
        raise HTTPException(status_code=404, detail=f"Fichier non trouve: {path}")
    # Limiter la taille (2 Mo max)
    size = os.path.getsize(full)
    if size > 2 * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"Fichier trop volumineux ({size} bytes)")
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"path": path, "content": content, "size": size}


class CodeWriteRequest(BaseModel):
    path: str
    content: str

@router.post("/api/code/write")
async def api_code_write(data: CodeWriteRequest, key: str = ""):
    """Ecrit/modifie un fichier du projet."""
    _ck(key)
    full = _safe_path(data.path)
    # Interdire l'ecriture dans certains fichiers sensibles
    basename = os.path.basename(full)
    if basename in (".env", "credentials.json", "id_rsa", "id_ed25519"):
        raise HTTPException(status_code=403, detail="Modification de ce fichier interdite")
    # Creer le repertoire parent si necessaire
    parent = os.path.dirname(full)
    if not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)
    # Backup avant ecriture
    if os.path.exists(full):
        backup = full + ".bak"
        try:
            import shutil
            shutil.copy2(full, backup)
        except Exception:
            pass
    try:
        with open(full, "w", encoding="utf-8") as f:
            f.write(data.content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    logger.info(f"[CodeCenter] Fichier modifie: {data.path}")
    return {"message": f"Fichier {data.path} sauvegarde", "path": data.path}


class CodeGenerateRequest(BaseModel):
    prompt: str
    current_file: str = ""
    current_content: str = ""

@router.post("/api/code/generate")
async def api_code_generate(data: CodeGenerateRequest, key: str = ""):
    """Genere du code via IA a partir d'une instruction en langage naturel."""
    _ck(key)

    # Construire le contexte pour l'IA
    system_prompt = """Tu es un assistant de developpement expert Python/FastAPI pour le projet Tendo.

PROJET TENDO :
- Framework : FastAPI + SQLAlchemy 2.0 async + PostgreSQL
- Scheduler : APScheduler
- WhatsApp : Meta Cloud API
- IA : Groq (Llama 3.3), Gemini Flash, DeepSeek, Claude
- Structure : app/routers/, app/services/, app/models/, app/utils/
- Docker : docker-compose.yml, Dockerfile
- Serveur : /opt/tendo/ sur Debian/Ubuntu

REGLES DE GENERATION :
1. Genere du code Python 3.11+ fonctionnel et complet
2. Respecte les conventions du projet (async, SQLAlchemy 2.0, type hints)
3. Si on te demande de modifier un fichier existant, retourne le fichier COMPLET modifie
4. Si on te demande un nouveau fichier, retourne le code complet
5. Reponds en francais pour les explications
6. Inclus les imports necessaires
7. Ne genere JAMAIS de code malveillant ou destructeur

FORMAT DE REPONSE OBLIGATOIRE :
Tu dois repondre avec exactement ce format JSON (pas de markdown, pas de ```json) :
{"response": "explication courte de ce que fait le code", "code": "le code complet ici", "target_file": "chemin/relatif/du/fichier.py", "action": "replace"}

Pour "action" : "replace" = remplacer tout le fichier, "append" = ajouter a la fin, "insert" = inserer le code
Si tu n'as pas de code a generer (question, explication), utilise : {"response": "ta reponse", "code": "", "target_file": "", "action": ""}"""

    user_prompt = data.prompt
    if data.current_file:
        user_prompt += f"\n\nFichier actuellement ouvert : {data.current_file}"
    if data.current_content:
        # Limiter le contenu envoye pour ne pas exploser les tokens
        content_preview = data.current_content[:8000]
        if len(data.current_content) > 8000:
            content_preview += f"\n\n... (tronque, {len(data.current_content)} caracteres au total)"
        user_prompt += f"\n\nContenu actuel du fichier :\n```\n{content_preview}\n```"

    # Essayer les LLMs en cascade
    import httpx
    import json

    raw_response = None

    # 1. Groq (gratuit, rapide)
    if settings.groq_api_key:
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                r = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {settings.groq_api_key}", "Content-Type": "application/json"},
                    json={"model": "llama-3.3-70b-versatile", "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ], "max_tokens": 4000, "temperature": 0.3},
                )
                if r.status_code == 200:
                    raw_response = r.json()["choices"][0]["message"]["content"].strip()
                    logger.info(f"[CodeCenter] Groq OK ({len(raw_response)} chars)")
        except Exception as e:
            logger.error(f"[CodeCenter] Groq erreur: {e}")

    # 2. Gemini Flash (gratuit)
    if not raw_response and settings.gemini_api_key:
        try:
            from app.services.claude import _get_gemini
            client = _get_gemini()
            if client:
                def _sync():
                    from google.genai import types
                    resp = client.models.generate_content(
                        model="gemini-2.0-flash",
                        contents=[types.Content(role="user", parts=[types.Part(text=user_prompt)])],
                        config=types.GenerateContentConfig(system_instruction=system_prompt, max_output_tokens=4000, temperature=0.3),
                    )
                    return resp.text.strip()
                raw_response = await asyncio.to_thread(_sync)
                logger.info(f"[CodeCenter] Gemini OK ({len(raw_response)} chars)")
        except Exception as e:
            logger.error(f"[CodeCenter] Gemini erreur: {e}")

    # 3. DeepSeek (low-cost)
    if not raw_response and settings.deepseek_api_key:
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                r = await client.post(
                    "https://api.deepseek.com/chat/completions",
                    headers={"Authorization": f"Bearer {settings.deepseek_api_key}", "Content-Type": "application/json"},
                    json={"model": "deepseek-chat", "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ], "max_tokens": 4000, "temperature": 0.3},
                )
                if r.status_code == 200:
                    raw_response = r.json()["choices"][0]["message"]["content"].strip()
                    logger.info(f"[CodeCenter] DeepSeek OK ({len(raw_response)} chars)")
        except Exception as e:
            logger.error(f"[CodeCenter] DeepSeek erreur: {e}")

    if not raw_response:
        raise HTTPException(status_code=503, detail="Aucun service IA disponible. Verifiez les cles API.")

    # Parser la reponse JSON de l'IA
    try:
        # Nettoyer : l'IA peut envelopper dans ```json ... ```
        cleaned = raw_response
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
        result = json.loads(cleaned)
        return {
            "response": result.get("response", ""),
            "code": result.get("code", ""),
            "target_file": result.get("target_file", ""),
            "action": result.get("action", "replace"),
        }
    except json.JSONDecodeError:
        # Si l'IA n'a pas respecte le format JSON, retourner comme texte
        # Essayer d'extraire un bloc de code
        code_block = ""
        if "```" in raw_response:
            parts = raw_response.split("```")
            if len(parts) >= 3:
                code_part = parts[1]
                # Retirer le tag de langage (python, json, etc.)
                if "\n" in code_part:
                    code_block = code_part.split("\n", 1)[1].strip()
                else:
                    code_block = code_part.strip()
        return {
            "response": raw_response[:500] if not code_block else raw_response.split("```")[0].strip()[:300],
            "code": code_block,
            "target_file": data.current_file,
            "action": "replace" if code_block else "",
        }


class CodeExecRequest(BaseModel):
    command: str

@router.post("/api/code/exec")
async def api_code_exec(data: CodeExecRequest, key: str = ""):
    """Execute une commande shell sur le serveur (securisee)."""
    _ck(key)

    cmd = data.command.strip()

    # Commandes interdites (destructrices)
    dangerous = ["rm -rf /", "mkfs", "dd if=", ":(){", "chmod -R 777 /",
                 "wget|sh", "curl|sh", "shutdown", "reboot", "halt",
                 "kill -9 1", "init 0", "init 6"]
    cmd_lower = cmd.lower()
    for d in dangerous:
        if d in cmd_lower:
            raise HTTPException(status_code=403, detail=f"Commande interdite: contient '{d}'")

    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=30, cwd=_PROJECT_ROOT,
        )
        output = result.stdout
        if result.stderr:
            output += "\n--- STDERR ---\n" + result.stderr
        if result.returncode != 0:
            output += f"\n[exit code: {result.returncode}]"
        # Limiter la sortie
        if len(output) > 10000:
            output = output[:10000] + f"\n... (tronque, {len(output)} chars total)"
        return {"output": output or "(aucune sortie)", "returncode": result.returncode}
    except subprocess.TimeoutExpired:
        return {"output": "TIMEOUT: commande interrompue apres 30 secondes", "returncode": -1}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/code/restart")
async def api_code_restart(key: str = ""):
    """Redemarre l'application Tendo (signal au process uvicorn)."""
    _ck(key)
    logger.info("[CodeCenter] Redemarrage demande par admin")
    # Dans un conteneur Docker, on ne peut pas docker compose depuis l'interieur.
    # On peut envoyer un signal au process principal pour forcer un restart gracieux.
    try:
        import signal
        os.kill(1, signal.SIGHUP)  # PID 1 = uvicorn dans le conteneur
        return {"message": "Signal de redemarrage envoye. Le conteneur doit etre relance depuis l'hote avec: docker compose restart tendo-api"}
    except Exception as e:
        return {"message": f"Pour redemarrer, executez sur le serveur: cd /opt/tendo && docker compose restart tendo-api\nErreur signal: {e}"}


# ══════════════════════════════════════════════════════════════════════════════
# API Git (commit/push depuis le Code Center)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/api/code/git/status")
async def api_git_status(key: str = ""):
    """Retourne le statut git du projet."""
    _ck(key)
    try:
        status = subprocess.run(
            "git status --porcelain", shell=True, capture_output=True, text=True,
            timeout=10, cwd=_PROJECT_ROOT,
        )
        branch = subprocess.run(
            "git branch --show-current", shell=True, capture_output=True, text=True,
            timeout=5, cwd=_PROJECT_ROOT,
        )
        log = subprocess.run(
            "git log --oneline -10", shell=True, capture_output=True, text=True,
            timeout=10, cwd=_PROJECT_ROOT,
        )
        diff_stat = subprocess.run(
            "git diff --stat", shell=True, capture_output=True, text=True,
            timeout=10, cwd=_PROJECT_ROOT,
        )
        # Parse status lines
        files = []
        for line in status.stdout.strip().split("\n"):
            if line.strip():
                st = line[:2].strip()
                fn = line[3:].strip()
                files.append({"status": st, "file": fn})
        return {
            "branch": branch.stdout.strip(),
            "files": files,
            "clean": len(files) == 0,
            "diff_stat": diff_stat.stdout.strip(),
            "recent_commits": log.stdout.strip().split("\n") if log.stdout.strip() else [],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class GitCommitRequest(BaseModel):
    message: str
    files: Optional[List[str]] = None  # None = all changed files

@router.post("/api/code/git/commit")
async def api_git_commit(data: GitCommitRequest, key: str = ""):
    """Commit les modifications."""
    _ck(key)
    if not data.message.strip():
        raise HTTPException(status_code=400, detail="Message de commit requis")
    try:
        # Stage files
        if data.files:
            for f in data.files:
                subprocess.run(f"git add {f}", shell=True, cwd=_PROJECT_ROOT, timeout=5)
        else:
            subprocess.run("git add -A", shell=True, cwd=_PROJECT_ROOT, timeout=10)

        # Commit
        result = subprocess.run(
            ["git", "commit", "-m", data.message],
            capture_output=True, text=True, timeout=15, cwd=_PROJECT_ROOT,
        )
        if result.returncode != 0:
            return {"success": False, "output": result.stderr or result.stdout}

        logger.info(f"[CodeCenter] Git commit: {data.message[:80]}")
        return {"success": True, "output": result.stdout}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/code/git/push")
async def api_git_push(key: str = ""):
    """Push les commits vers GitHub."""
    _ck(key)
    try:
        result = subprocess.run(
            "git push origin main", shell=True, capture_output=True, text=True,
            timeout=60, cwd=_PROJECT_ROOT,
        )
        output = result.stdout + "\n" + result.stderr
        if result.returncode != 0:
            return {"success": False, "output": output.strip()}
        logger.info("[CodeCenter] Git push OK")
        return {"success": True, "output": output.strip() or "Push OK"}
    except subprocess.TimeoutExpired:
        return {"success": False, "output": "Timeout (60s) — verifiez la connexion"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
