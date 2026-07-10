/* ============================================================
   fund-dashboard Supabase Client
   ============================================================
   统一管理所有 Supabase 数据读写，按 auth.uid() 做用户隔离。
   依赖: CDN 加载的 @supabase/supabase-js@2（index.html L1298）
   全局配置: window.SUPABASE_URL, window.SUPABASE_ANON_KEY（index.html L1300-1301）
   ============================================================ */

(function () {
  'use strict';

  var _sb = null;

  /* ---------- 初始化 ---------- */
  window.initSupabase = function () {
    if (_sb) return _sb;
    if (typeof supabase === 'undefined') {
      throw new Error('Supabase SDK not loaded. Ensure @supabase/supabase-js@2 CDN is loaded before supabase-client.js');
    }
    var url = window.SUPABASE_URL;
    var key = window.SUPABASE_ANON_KEY;
    if (!url || !key) {
      throw new Error('window.SUPABASE_URL or window.SUPABASE_ANON_KEY not set');
    }
    _sb = supabase.createClient(url, key, {
      auth: {
        persistSession: true,
        autoRefreshToken: true,
        detectSessionInUrl: true,
      },
    });
    return _sb;
  };

  /* ---------- 获取当前用户 ---------- */
  window.getCurrentUser = function () {
    var sb = initSupabase();
    return sb.auth.getSession().then(function (result) {
      var session = result && result.data && result.data.session;
      if (!session || !session.user) return null;
      return { id: session.user.id, email: session.user.email };
    });
  };

  /* ---------- 用户活跃标记 ---------- */
  window.markUserActive = function (userId) {
    var sb = initSupabase();
    return sb.rpc('mark_user_active', { p_user_id: userId }).then(function () {
      // 静默，不报错
    }).catch(function () {});
  };

  /* ---------- 事件日志 ---------- */
  window.logEvent = function (eventType) {
    var sb = initSupabase();
    return sb.auth.getSession().then(function (result) {
      var user = result && result.data && result.data.session && result.data.session.user;
      if (!user) return;
      return sb.from('event_logs').insert({
        user_id: user.id,
        event_type: eventType,
        created_at: new Date().toISOString(),
      });
    }).catch(function () {});
  };

  /* ========== 持仓 ========== */

  /**
   * 从 Supabase 加载当前用户的持仓列表
   * 返回 Promise<Array<fundObject>>
   */
  window.supabaseLoadHoldings = function () {
    var sb = initSupabase();
    return sb.auth.getSession().then(function (result) {
      var user = result && result.data && result.data.session && result.data.session.user;
      if (!user) return [];
      return sb.from('fund_holdings')
        .select('*')
        .eq('user_id', user.id)
        .order('fund_code')
        .then(function (res) {
          if (res.error) {
            console.warn('supabaseLoadHoldings error:', res.error.message);
            return [];
          }
          console.log('[supabaseLoadHoldings] raw rows:', res.data ? res.data.length : 0, res.data ? JSON.stringify(res.data[0]).substring(0, 300) : 'null');
          return (res.data || []).map(function (h) {
            return {
              id: h.fund_code,
              code: h.fund_code,
              name: h.fund_name,
              amount: h.amount,
              dailyPnL: h.day_pnl,
              dailyPct: h.day_pnl_pct,
              holdingPnL: h.holding_pnl,
              holdingPct: h.holding_pnl_pct,
              costBasis: h.cost_basis,
              holdingShares: h.holding_shares,
              sector: { n: h.sector_name || '' },
              sectorSource: h.sector_source || '',
              buyDate: h.buy_date || '',
              transactions: h.transactions || [],
              pendingTransactions: h.pending_transactions || [],
            };
          });
        });
    });
  };

  /**
   * 保存当前用户全部持仓到 Supabase（全量覆盖）
   * funds: Array<fundObject>
   */
  window.supabaseSaveHoldings = function (funds) {
    if (!funds || !funds.length) return Promise.resolve();
    var sb = initSupabase();
    return sb.auth.getSession().then(function (result) {
      var user = result && result.data && result.data.session && result.data.session.user;
      if (!user) return;
      // 先删除该用户所有旧记录，再批量插入新记录
      return sb.from('fund_holdings').delete().eq('user_id', user.id).then(function () {
        var rows = funds.map(function (f) {
          return {
            user_id: user.id,
            fund_code: f.id || f.code || '',
            fund_name: f.name || '',
            amount: f.amount || 0,
            day_pnl: f.dailyPnL || 0,
            day_pnl_pct: f.dailyPct || 0,
            holding_pnl: f.holdingPnL || 0,
            holding_pnl_pct: f.holdingPct || 0,
            cost_basis: f.costBasis || null,
            holding_shares: f.holdingShares || null,
            sector_name: (f.sector && f.sector.n) || '',
            sector_source: f.sectorSource || '',
            buy_date: f.buyDate || null,
            transactions: f.transactions || [],
            pending_transactions: f.pendingTransactions || [],
          };
        });
        return sb.from('fund_holdings').insert(rows);
      });
    }).catch(function (e) {
      console.warn('supabaseSaveHoldings error:', e.message);
    });
  };

  /* ========== 盈亏日历 ========== */

  /**
   * 从 Supabase 加载盈亏日历数据
   * 返回 Promise<calendarOverridesObject>（key: "YYYY-MM-DD" → value: number）
   */
  window.supabaseLoadCalendar = function () {
    var sb = initSupabase();
    return sb.auth.getSession().then(function (result) {
      var user = result && result.data && result.data.session && result.data.session.user;
      if (!user) return {};
      return sb.from('calendar_data')
        .select('data')
        .eq('user_id', user.id)
        .maybeSingle()
        .then(function (res) {
          if (res.error || !res.data) return {};
          try {
            return JSON.parse(res.data.data || '{}');
          } catch (e) {
            return {};
          }
        });
    });
  };

  /**
   * 保存盈亏日历到 Supabase（全量覆盖）
   * calData: { "2026-07-01": -4037.32, ... }
   */
  window.supabaseSaveCalendar = function (calData) {
    var sb = initSupabase();
    return sb.auth.getSession().then(function (result) {
      var user = result && result.data && result.data.session && result.data.session.user;
      if (!user) return;
      var payload = {
        user_id: user.id,
        data: JSON.stringify(calData),
        updated_at: new Date().toISOString(),
      };
      // upsert: 如果该用户已有记录则更新
      return sb.from('calendar_data').upsert(payload, { onConflict: 'user_id' });
    }).catch(function (e) {
      console.warn('supabaseSaveCalendar error:', e.message);
    });
  };

  /* ========== 用户设置 ========== */

  /**
   * 加载用户设置
   * 返回 Promise<{theme, hidden_columns, thresholds}>
   */
  window.supabaseLoadSettings = function () {
    var sb = initSupabase();
    return sb.auth.getSession().then(function (result) {
      var user = result && result.data && result.data.session && result.data.session.user;
      if (!user) return {};
      return sb.from('user_settings')
        .select('*')
        .eq('user_id', user.id)
        .maybeSingle()
        .then(function (res) {
          if (res.error || !res.data) return {};
          return {
            theme: res.data.theme || '',
            hidden_columns: res.data.hidden_columns || [],
            thresholds: res.data.thresholds || {},
          };
        });
    });
  };

  /**
   * 保存用户设置
   * settings: {theme?, hidden_columns?, thresholds?}
   */
  window.supabaseSaveSettings = function (settings) {
    var sb = initSupabase();
    return sb.auth.getSession().then(function (result) {
      var user = result && result.data && result.data.session && result.data.session.user;
      if (!user) return;
      var payload = {
        user_id: user.id,
        updated_at: new Date().toISOString(),
      };
      if (settings.theme !== undefined) payload.theme = settings.theme;
      if (settings.hidden_columns !== undefined) payload.hidden_columns = settings.hidden_columns;
      if (settings.thresholds !== undefined) payload.thresholds = settings.thresholds;
      return sb.from('user_settings').upsert(payload, { onConflict: 'user_id' });
    }).catch(function (e) {
      console.warn('supabaseSaveSettings error:', e.message);
    });
  };

})();
