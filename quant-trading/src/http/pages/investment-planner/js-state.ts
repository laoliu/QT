/** Investment Planner — global state variables, constants, and preset configurations */
export function getStateJs(): string {
  return `
var currentPlan = null;
var currentPlanVersion = null;
var signalHistory = {paper:[],live:[]};
var _lastPortfolioSignals = {paper:[],live:[]};
var institutionScanCache = null;
var institutionHistoryCache = null;
var institutionHistoryPeakSortBy = {order: 'count', customer: 'count', patent: 'count', restructure: 'count'};
var institutionHistoryCachedMetrics = null;
var institutionDetailCache = {};
var institutionMoatCache = {};
var viewingInstitutionHistoryId = null;
var currentInstitutionTop3Rows = [];
var currentInstitutionTop3HistoryId = null;
var selectedInstitutionTop3Symbol = '';
var _instTop3Seq = 0;
var _instPresetInitialized = false;
var ACC_TOP3_THRESHOLDS_KEY = 'openclaw-acc-top3-thresholds-v1';
var ACC_TOP3_PRESET_KEY = 'openclaw-acc-top3-preset-v1';
var ACC_TOP3_PRESETS = {
  stable:{
    pre_accum_days:12,
    pre_accum_count_min:3,
    pre_accum_volume_ratio:1.8,
    rush_change_pct:6,
    rush_volume_ratio:2.5,
    rush_turnover_pct:4,
    pre_surge_chip_min:10,
    pre_surge_volume_price_min:8,
    pre_surge_turnover_min:8
  },
  swing:{
    pre_accum_days:10,
    pre_accum_count_min:2,
    pre_accum_volume_ratio:1.5,
    rush_change_pct:5,
    rush_volume_ratio:2,
    rush_turnover_pct:3,
    pre_surge_chip_min:8,
    pre_surge_volume_price_min:6,
    pre_surge_turnover_min:6
  },
  shortline:{
    pre_accum_days:8,
    pre_accum_count_min:2,
    pre_accum_volume_ratio:1.3,
    rush_change_pct:3.5,
    rush_volume_ratio:1.6,
    rush_turnover_pct:2,
    pre_surge_chip_min:6,
    pre_surge_volume_price_min:5,
    pre_surge_turnover_min:5
  }
};

var INSTITUTION_PRESET_KEY = 'openclaw-inst-threshold-preset';
var INSTITUTION_THRESHOLDS_KEY = 'openclaw-inst-threshold-values';
var INSTITUTION_CUSTOM_PRESETS_KEY = 'openclaw-inst-custom-presets-v1';
var INSTITUTION_THRESHOLD_PRESETS = {
  conservative:{
    pre_accum_days:12,
    pre_accum_count_min:3,
    pre_accum_volume_ratio:1.8,
    rush_change_pct:6,
    rush_volume_ratio:2.5,
    rush_turnover_pct:4
  },
  balanced:{
    pre_accum_days:10,
    pre_accum_count_min:2,
    pre_accum_volume_ratio:1.5,
    rush_change_pct:5,
    rush_volume_ratio:2,
    rush_turnover_pct:3
  },
  aggressive:{
    pre_accum_days:8,
    pre_accum_count_min:2,
    pre_accum_volume_ratio:1.3,
    rush_change_pct:3.5,
    rush_volume_ratio:1.6,
    rush_turnover_pct:2
  }
};
var institutionCustomThresholdPresets = {};
var _dailyPickState = null;
var _dailyPickRunning = false;
var DAILY_PICK_HISTORY_KEY = 'openclaw-daily-pick-history-v1';
var _dailyPickHistory = [];
var _lastRecommendData = null;
var _recExecRunning = false;
var _recExecuted = false;
var _recCurrentBatch = 1;  // which batch was last executed (1, 2, or 3)
var _recBatchHistory = [];  // track batch execution: [{batch, pct, orders}]
var _addPosRecData = null;
var _addPosExecRunning = false;
var _addPosExecuted = false;
var _accTop3State = null;
var _accScanState = null;
`;
}
