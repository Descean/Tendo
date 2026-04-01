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

from app.config import settings
from app.models.notification import Notification
from app.models.publication import Publication
from app.models.subscription import Subscription
from app.models.user import User, SubscriptionStatus
from app.utils.db import get_db
from app.utils.logger import logger

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
    <div class="max-h-96 overflow-y-auto">
      <table><thead class="sticky top-0 bg-[#0F172A]"><tr><th class="w-10">ID</th><th class="w-24">Source</th><th class="w-20">Type</th><th>Titre</th><th class="w-24">Budget</th><th class="w-20">Deadline</th><th class="w-8"></th></tr></thead>
        <tbody><template x-for="p in fPubs.slice(0,200)" :key="p.id"><tr>
          <td class="font-mono text-slate-600" x-text="p.id"></td>
          <td><span class="src-pill" x-text="p.source"></span></td>
          <td class="text-sky-400 text-[11px]" x-text="p.document_type||'-'"></td>
          <td class="text-slate-200 max-w-md truncate" x-text="p.title"></td>
          <td class="font-mono text-amber-400" x-text="p.budget?xof(p.budget):'-'"></td>
          <td class="text-slate-500 text-[11px]" x-text="fdate(p.deadline)"></td>
          <td><button @click="delPub(p)" class="text-slate-600 hover:text-red-400 transition"><i class="fa-solid fa-trash-can text-[10px]"></i></button></td>
        </tr></template></tbody>
      </table>
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
      <thead><tr><th>ID</th><th>Telephone</th><th>Nom</th><th>Entreprise</th><th>Email</th><th>Statut</th><th>Plan</th><th>Secteurs</th><th>Inscription</th><th>Fin essai</th><th>Actions</th></tr></thead>
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
  ],
  S:{},users:[],pubs:[],sys:{},logs:[],
  usrQ:'',usrF:'',pubQ:'',pubSrcF:'',pubTypeF:'',
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
    await Promise.all([this.loadStats(),this.loadUsers(),this.loadPubs(),this.loadSys(),this.loadLogs()]);
    this.lastRefresh=new Date().toLocaleTimeString('fr-FR');
    this.busy=false;this.$nextTick(()=>{this.drawCharts()});
  },

  async api(p){const r=await fetch('/admin/api'+p+(p.includes('?')?'&':'?')+'key='+encodeURIComponent(this.apiKey));if(!r.ok)throw new Error(r.status);return r.json()},
  async loadStats(){try{this.S=await this.api('/stats')}catch(e){}},
  async loadUsers(){try{const d=await this.api('/users?limit=1000');this.users=d.users??d}catch(e){}},
  async loadPubs(){try{const d=await this.api('/publications?limit=2000');this.pubs=d.publications??d}catch(e){}},
  async loadSys(){try{this.sys=await this.api('/system')}catch(e){}},
  async loadLogs(){try{const d=await this.api('/logs');this.logs=d.logs??[]}catch(e){}},

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
         "is_active": u.is_active,
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
         "authority_name": p.authority_name,
         "deadline": p.deadline.isoformat() if p.deadline else None,
         "created_at": p.created_at.isoformat() if p.created_at else None,
         "pdf_url": p.pdf_url, "is_processed": p.is_processed}
        for p in res.scalars().all()
    ]}


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
        from app.utils.db import AsyncSessionLocal
        try:
            from app.services.pdf_pipeline import process_publication_pdfs
            async with AsyncSessionLocal() as db:
                count = await process_publication_pdfs(db)
                logger.info(f"[Admin] Pipeline PDF terminé: {count} traitées")
        except Exception as e:
            logger.error(f"[Admin] Erreur pipeline PDF: {e}")
    asyncio.create_task(_run())
    return {"status": "started", "message": "Pipeline PDF lancé en arrière-plan"}


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
