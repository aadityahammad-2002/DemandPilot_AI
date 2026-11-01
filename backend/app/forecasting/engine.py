"""Reproducible Phase 3 forecasting pipeline, isolated from PostgreSQL and hidden truth."""
from __future__ import annotations
from collections import defaultdict
from datetime import date, timedelta
import csv
import hashlib
import json
import math
from pathlib import Path
import random
import math

from app.forecasting.config import ForecastConfig, chronological_split
from app.forecasting.data import load_history, master_info, select_pairs
from app.forecasting.features import (FEATURE_NAMES, censored, feature_vector,
                                       infer_path)
from app.forecasting.evaluation import choose_method, evaluate
from app.ids import forecast_id

METHODS = ('seasonal_naive', 'moving_average', 'croston_sba', 'ml_hgb')
CSV_COLUMNS = ('forecast_id','run_id','sku_id','store_id','forecast_origin',
               'target_date','horizon','predicted_units','model_version',
               'status','split','feature_cutoff','feature_source','selection_status')
SCORING_COLUMNS = ('forecast_id','split','method','horizon','predicted_units',
                   'actual_units','censored_target','segment','eligible')


def _json(value: object) -> str:
    return json.dumps(value,sort_keys=True,indent=2,allow_nan=False)+'\n'


def _file_sha(path: Path) -> str:
    with path.open('rb') as fp:
        return hashlib.file_digest(fp,'sha256').hexdigest()


def train_global(history: dict, products: dict, stores: dict, split: dict,
                 cfg: ForecastConfig, promo_schedule: dict | None = None):
    """Fit only eligible uncensored *training* targets; sample bounded reservoir."""
    import numpy as np
    from sklearn.ensemble import HistGradientBoostingRegressor
    train_end=split['train'][1]
    rng=random.Random(cfg.seed)
    X=[];y=[];total=0;censored_skipped=0
    for (store_id,sku_id), rows in sorted(history.items()):
        past=[]
        for row in rows:
            if row['date'] >= train_end: break
            if len(past)>=cfg.min_history:
                if censored(row):
                    censored_skipped += 1
                else:
                    from app.forecasting.promotions import known_multiplier
                    planned=known_multiplier(promo_schedule or {}, store_id, sku_id,
                                             row['date'], row['date']-timedelta(days=1))
                    features=feature_vector(past,row['date'],products[sku_id],stores[store_id],planned)
                    total += 1
                    if len(X)<cfg.max_train_rows:
                        X.append(features);y.append(float(row['sales']))
                    else:
                        slot=rng.randrange(total)
                        if slot<cfg.max_train_rows:
                            X[slot]=features;y[slot]=float(row['sales'])
            past.append(row)
    if len(y)<40:
        raise ValueError(f'insufficient uncensored training rows: {len(y)} (need >=40)')
    model=HistGradientBoostingRegressor(
        max_iter=cfg.model_iterations, max_leaf_nodes=15, min_samples_leaf=5,
        l2_regularization=0.5, learning_rate=0.08, random_state=cfg.seed,
        early_stopping=False)
    model.fit(np.asarray(X,dtype='float64'),np.asarray(y,dtype='float64'))
    # Explain model with bounded training-only permutation importance (not test leakage).
    from sklearn.inspection import permutation_importance
    size=min(len(y),160)
    imp=permutation_importance(model,np.asarray(X[:size],dtype='float64'),
                               np.asarray(y[:size],dtype='float64'),
                               scoring='neg_mean_absolute_error',n_repeats=2,
                               random_state=cfg.seed,n_jobs=1)
    importance=sorted([{'feature':key,'importance':round(float(value),6)}
                       for key,value in zip(FEATURE_NAMES,imp.importances_mean)],
                      key=lambda e:(-e['importance'],e['feature']))
    return model, {'rows_seen':total,'rows_used':len(y),'censored_targets_skipped':censored_skipped,
                   'importance_training_only':importance,
                   'feature_names':list(FEATURE_NAMES),'seed':cfg.seed}


def _split_origins(split: tuple[date,date], cfg: ForecastConfig) -> list[date]:
    start,end=split
    origins=[]
    current=start-timedelta(days=1)
    while current<end and len(origins)<cfg.max_origins_per_split:
        origins.append(current)
        current+=timedelta(days=cfg.origin_stride)
    return origins


def produce_rows(history: dict, products: dict, stores: dict, split: dict,
                 model, cfg: ForecastConfig, run_id: str,
                 promo_schedule: dict | None = None) -> list[dict]:
    """Walk-forward origins: history strictly <= origin, recursive later steps."""
    records=[]
    for (store_id,sku_id), rows in sorted(history.items()):
        product=products[sku_id]; store=stores[store_id]
        for split_name in ('validation','test'):
            lo,hi=split[split_name]
            for origin in _split_origins((lo,hi),cfg):
                past=[r for r in rows if r['date']<=origin]
                if len(past)<cfg.min_history:
                    continue
                targets={h:(origin+timedelta(days=h)) for h in cfg.horizons
                         if lo<=origin+timedelta(days=h)<hi}
                if not targets:
                    continue
                horizon=max(targets)
                for method in METHODS:
                    path=infer_path(past,origin,horizon,method,model,product,store,promo_schedule)
                    for h,target in sorted(targets.items()):
                        records.append({
                            'run_id':run_id,'sku_id':sku_id,'store_id':store_id,
                            'forecast_origin':origin.isoformat(),
                            'target_date':target.isoformat(),'horizon':h,
                            'predicted_units':path[h-1],
                            'model_version':f'phase3-{method}-v1',
                            'status':'VALIDATED','split':split_name,
                            'feature_cutoff':origin.isoformat(),
                            'feature_source':'public_sales_history_only',
                            'selection_status':'CANDIDATE',
                            'method':method,
                        })
    if not records: raise ValueError('no validation/test forecasts produced')
    return records


def attach_evaluation(rows: list[dict], history: dict) -> list[dict]:
    """Evaluation-only lookup: called only AFTER prediction CSV is frozen."""
    lookup={key:{r['date'].isoformat():r for r in seq} for key,seq in history.items()}
    scoring=[]
    for r in rows:
        target=lookup[(r['store_id'],r['sku_id'])].get(r['target_date'])
        if target is None:
            continue
        origin=date.fromisoformat(r['forecast_origin'])
        previous=[x for x in history[(r['store_id'],r['sku_id'])] if x['date']<=origin]
        known=[x for x in previous[-28:] if not censored(x)]
        true_zeros=sum(x['sales']==0 for x in known)
        segment='intermittent' if known and true_zeros/len(known)>=.45 else 'regular'
        # Seasonal naive denominator requires *calendar* lag-7 alignment.
        # Skipping censored days must never collapse the time axis.
        historical={x['date']:x['sales'] for x in previous if not censored(x)}
        naive_errors=[abs(value-historical[day-timedelta(days=7)])
                      for day,value in historical.items()
                      if day-timedelta(days=7) in historical]
        scale=sum(naive_errors)/len(naive_errors) if naive_errors else None
        scoring.append({**r,'actual_units':target['sales'],
                        'censored_target':censored(target),'segment':segment,
                        'mase_scale':scale})
    return scoring


def assign_forecast_ids(rows: list[dict]) -> None:
    """Stable deterministic sequence per store/SKU/origin month/model version."""
    count=defaultdict(int)
    method_version={method:i+1 for i,method in enumerate(METHODS)}
    rows.sort(key=lambda r:(r['store_id'],r['sku_id'],r['method'],
                            r['forecast_origin'],r['target_date'],r['horizon']))
    for r in rows:
        month=r['forecast_origin'][:7]
        key=(r['store_id'],r['sku_id'],r['method'],month)
        count[key]+=1
        version=method_version[r['method']]
        # Method version=1 gives official first identity, later methods get -Vnnnn.
        r['forecast_id']=forecast_id(r['store_id'],r['sku_id'],month,
                                     version=version,sequence=count[key])
    identifiers=[r['forecast_id'] for r in rows]
    if len(set(identifiers))!=len(identifiers):
        raise ValueError('forecast ID collision')


def _write_csv(path: Path, rows: list[dict]) -> None:
    with path.open('w',newline='',encoding='utf-8') as fp:
        w=csv.DictWriter(fp,fieldnames=CSV_COLUMNS,lineterminator='\n')
        w.writeheader()
        for r in rows: w.writerow({k:r[k] for k in CSV_COLUMNS})


def temporal_integrity(rows: list[dict]) -> dict:
    """Structural leakage contract for frozen forecast predictions."""
    count=0
    for row in rows:
        origin=date.fromisoformat(row['forecast_origin'])
        target=date.fromisoformat(row['target_date'])
        cutoff=date.fromisoformat(row['feature_cutoff'])
        if target != origin+timedelta(days=int(row['horizon'])) or cutoff>origin:
            raise ValueError('forecast target alignment or training cutoff violated')
        if row['feature_source']!='public_sales_history_only':
            raise ValueError('unapproved feature source for prediction')
        count+=1
    return {'status':'PASS','checked_predictions':count,
            'rule':'target_date=origin+horizon; feature_cutoff<=origin',
            'causal_stockout_fallback':'past-only same-weekday/recent available observations'}


def matched_censor_ablation(scoring: list[dict], winner: str, history: dict,
                            products: dict, stores: dict, model,
                            promotion_calendar: dict | None = None,
                            *, max_comparisons:int=500) -> dict:
    """Freeze the model and target keys, vary only causal censor correction.

    The same fitted ML model is retained; this is an *inference ablation*, not
    a causal claim about retraining the whole system.
    """
    from app.forecasting.features import infer_path
    paired=[]
    for row in scoring:
        if row['method']!=winner or row['censored_target']:
            continue
        if len(paired)>=max_comparisons: break
        origin=date.fromisoformat(row['forecast_origin'])
        key=(row['store_id'],row['sku_id'])
        past=[r for r in history[key] if r['date']<=origin]
        no_correction=infer_path(past,origin,int(row['horizon']),winner,model,
              products[key[1]],stores[key[0]],promotion_calendar,correction_enabled=False)[-1]
        paired.append({'actual_units':row['actual_units'],
                       'predicted_units':no_correction,
                       'original_units':row['predicted_units'],
                       'censored_target':False,
                       'mase_scale':row.get('mase_scale')})
    from app.forecasting.evaluation import metrics
    same_samples={'without_correction':metrics(paired),
                  'with_correction':metrics([{**r,'predicted_units':r['original_units']} for r in paired])}
    return {'scope':'matched inference ablation, same fitted model/cutoff/target',
            'method':winner,'paired_uncensored_targets':len(paired),
            'sample_limit':max_comparisons,'metrics':same_samples}


def forecast(data_root: Path, out: Path, cfg: ForecastConfig,
             promotion_calendar: Path | None = None) -> dict:
    """No PostgreSQL writes; refuses to overwrite artifacts; no hidden labels loaded."""
    cfg.validate()
    data_root=data_root.resolve();out=out.resolve()
    if out.exists() and any(out.iterdir()):
        raise FileExistsError(f'output not empty: {out}')
    manifest=json.loads((data_root/'manifest.json').read_text(encoding='utf-8'))
    origin_cfg=manifest['config']
    start=date.fromisoformat(origin_cfg['start_date'])
    split=chronological_split(start,int(origin_cfg['days']))
    pairs=select_pairs(manifest,cfg.max_pairs)
    history,manifest=load_history(data_root,pairs)
    products,stores=master_info(data_root)
    from app.forecasting.promotions import read_calendar
    promo_schedule=read_calendar(promotion_calendar)
    # A future-sales edit must not change run identity or any pre-origin forecast.
    training_public=[(store,sku,r['date'].isoformat(),r['sales'],r['stockout'],
                      r['available'],r['promo'],r['multiplier'])
        for (store,sku),seq in sorted(history.items()) for r in seq
        if r['date']<split['train'][1]]
    input_signature=hashlib.sha256(_json({
        'forecast_config':cfg.as_dict(),
        'train_start':split['train'][0].isoformat(),
        'train_end':split['train'][1].isoformat(),
        'training_public':training_public,
        'public_master_features':sorted(
            (sku,products[sku]['category'],products[sku]['launch_date']) for _,sku in history),
        'public_store_features':sorted(
            (store,stores[store]['region']) for store,_ in history),
        'promotions':sorted((k[0],k[1],k[2].isoformat(),v[0].isoformat(),v[1])
            for k,v in promo_schedule.items()),
    }).encode()).hexdigest()
    run_id='FC-RUN-'+input_signature[:20].upper()
    # Exact ML results can drift across numpy/scikit-learn/OpenMP environments.
    # Record dependency versions for auditable cross-machine model comparisons.
    import numpy as np
    import sklearn
    runtime_versions={'numpy':np.__version__,'scikit_learn':sklearn.__version__}
    model,train_info=train_global(history,products,stores,split,cfg,promo_schedule)
    rows=produce_rows(history,products,stores,split,model,cfg,run_id,promo_schedule)
    assign_forecast_ids(rows)
    temporal_audit=temporal_integrity(rows)
    out.mkdir(parents=True,exist_ok=True)
    pred=out/'forecast_predictions.csv'
    # Freeze all predictions BEFORE consulting observed holdout target labels.
    _write_csv(pred,rows)
    scoring=attach_evaluation(rows,history)
    from app.forecasting.diagnostics import (validation_sample_keys,
        empirical_interval_diagnostics, validation_selection_stability)
    key_audit=validation_sample_keys(scoring)
    val_only=evaluate([r for r in scoring if r['split']=='validation'])
    winner=choose_method(val_only)
    ablation=matched_censor_ablation(scoring,winner,history,products,stores,model,promo_schedule)
    interval_diagnostics=empirical_interval_diagnostics(scoring,winner)
    stability=validation_selection_stability(scoring,winner)
    # Freeze a separate selection record; never mutate the frozen prediction file.
    summary=evaluate(scoring)
    selected=out/'forecast_selected.csv'
    _write_csv(selected,[{**r,'selection_status':'SELECTED'} for r in rows if r['method']==winner])
    with (out/'evaluation_scoring.csv').open('w',newline='',encoding='utf-8') as fp:
        w=csv.DictWriter(fp,fieldnames=SCORING_COLUMNS,lineterminator='\n')
        w.writeheader()
        for r in scoring:
            w.writerow({**{k:r.get(k,'') for k in SCORING_COLUMNS},'eligible':not r['censored_target']})
    import joblib
    joblib.dump(model,out/'forecast_model.joblib',compress=3)
    out_eval=out/'evaluation.json'
    out_eval.write_text(_json({'selection_method':'validation_only',
                                'selected_model':winner,'metrics':summary,
                                'temporal_integrity':temporal_audit,
                                'matched_censor_ablation':ablation,
                                'validation_target_keys':key_audit,
                                'validation_selection_stability':stability,
                                'uncertainty_diagnostics':interval_diagnostics,
                                'runtime_versions':runtime_versions,
                                'censored_targets_scored':False,
                                'zero_demand_scored_when_uncensored':True,
                                'oracle_truth_used':False}),encoding='utf-8')
    explain={'run_id':run_id,'approach':'rolling-origin recursive multi-step, no future sales',
             'train':train_info,
             'feature_lineage':'public sales_history plus sku.category/launch_date and store.region',
             'excluded':'restricted_ground_truth, negative_fixtures, sku.base_demand_rate, future promotion_flag, future sales, final sku.status',
             'stockout_policy':'impute censored history using prior uncensored days; exclude censored training labels and evaluation targets',
             'future_promotion_policy':('only planned promotions with known_at<=forecast_origin'
                 if promo_schedule else 'no public future plan supplied; future realized flags excluded'),
             'explanations':'permutation importance on bounded TRAINING sample; no causal claims'}
    (out/'explainability.json').write_text(_json(explain),encoding='utf-8')
    report={'version':'P3-v1','run_id':run_id,'generator_manifest_sha256':_file_sha(data_root/'manifest.json'),
            'forecast_config':cfg.as_dict(),'train_validation_test':{
                k:{'start':lo.isoformat(),'end_exclusive':hi.isoformat(),'days':(hi-lo).days}
                for k,(lo,hi) in split.items()},
            'selected_pairs':len(history),'full_coverage':len(history)==int(origin_cfg['sku_count'])*int(origin_cfg['store_count']),
            'full_scale_verification':'NOT_ASSESSED_BY_FORECAST_PIPELINE',
            'promotion_calendar_used':bool(promo_schedule),
            'runtime_versions':runtime_versions,'training':{'rows_seen':train_info['rows_seen'],
                 'rows_used':train_info['rows_used'],'censored_targets_skipped':train_info['censored_targets_skipped']},
            'rows_exported':len(rows),'scored_rows':len(scoring),'selected_model':winner,
            'model_versions':{name:f'phase3-{name}-v1' for name in METHODS},
            'run_id_scope':'run-scoped output; database insert must allocate nonconflicting forecast IDs across runs',
            'input_signature':input_signature,'database_mutations':False,
            'files':{k:_file_sha(out/k) for k in ('forecast_predictions.csv','forecast_selected.csv','evaluation_scoring.csv','evaluation.json','explainability.json','forecast_model.joblib')}}
    (out/'forecast_manifest.json').write_text(_json(report),encoding='utf-8')
    return report
