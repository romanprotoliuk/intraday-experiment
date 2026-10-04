"""Predeclared research hypotheses. No registration in the broker strategy registry."""
from dataclasses import replace,asdict
from datetime import timedelta

from trader.models import dt,iso,Unsafe
from trader.multistrategy.strategies import (Strategy,Evaluation,CandidateSignal,Parameters,STRATEGIES,fingerprint)
from .features import VERSION as FEATURES
from .regimes import eligible,VERSION as REGIMES

NEW=('opening_drive','failed_breakout','vwap_reclaim','trend_pullback','range_breakout','momentum_continuation','exhaustion_reversion')


class Hypothesis(Strategy):
    version='2.0.0-research'
    def __init__(self,name,parameters=None,threshold=1.):
        if name not in NEW or threshold not in (.8,1.,1.2):raise Unsafe('Unknown research hypothesis/threshold')
        super().__init__(parameters);self.id=name;self.threshold=threshold

    @property
    def fingerprint(self):return fingerprint(dict(id=self.id,version=self.version,parameters=asdict(self.parameters),threshold=self.threshold,features=FEATURES,regimes=REGIMES))

    def evaluate(self,frame,session):
        f=frame.values;b=frame.bars[-1];bs=frame.bars;p=self.parameters;t=self.threshold
        o,h,l,c=(float(b[k]) for k in ('open_price','high_price','low_price','close_price'))
        scale=f['short_range_scale'];rv=f.get('rvol_tod');atr=f.get('atr_14');n=len(bs)
        if not eligible(self.id,frame):return self.reject('REGIME_INELIGIBLE_OR_UNCERTAIN')
        ok=False;anchor=b['begins_at'];vwap_exit=True
        if self.id=='opening_drive':
            ok=(f['minutes_since_open'] in (10,15) and c-f['open']>=t*scale and rv is not None and rv>=1.2 and
                f.get('range_expansion') is not None and f['range_expansion']>=1 and f['both_above_vwap'] is True and f['body_fraction']>=.6)
            anchor='opening-drive'
        elif self.id=='failed_breakout':
            # Long failed downside OR breakout; no synthetic short execution.
            low=f.get('or15_low');high=f.get('or15_high')
            failed=next((i for i in range(3,n-1) if float(bs[i]['close_price'])<low-p.offset),None) if low else None
            ok=(failed is not None and n-1-failed<=p.timeout_bars and l<=low+scale*.2*t and c>max(low,o) and c<high)
            if failed is not None:anchor='failed-'+bs[failed]['begins_at']
            vwap_exit=False
        elif self.id=='vwap_reclaim':
            ok=(f['vwap_reclaim'] and f.get('momentum_5') is not None and f['momentum_5']>0 and
                f['body_fraction'] is not None and f['body_fraction']>=.5*t and rv is not None and rv>=1)
        elif self.id=='trend_pullback':
            ref=f.get('sma_6');slope=f.get('sma_6_slope')
            ok=(atr and ref is not None and slope is not None and slope>0 and l<=ref+.1*atr*t<c and
                c>o and c-f['vwap']<=1.5*atr and f['previous_bar_volume_ratio'] is not None and f['previous_bar_volume_ratio']>=1)
        elif self.id=='range_breakout':
            high=f.get('prior_range_high_6');low=f.get('prior_range_low_6')
            longhi=f.get('prior_range_high_12');longlo=f.get('prior_range_low_12')
            ok=(f['minutes_since_open']>=90 and high is not None and longhi is not None and
                high-low<=.6*t*(longhi-longlo) and c>high and rv is not None and rv>=1.2 and f['body_fraction']>=.6)
        elif self.id=='momentum_continuation':
            ok=(atr and f['consecutive_directional_bars']>=3 and f.get('momentum_15') is not None and
                f['momentum_15']*c/atr>=1.0*t and f.get('range_expansion') is not None and f['range_expansion']>=1.2 and
                rv is not None and rv>=1.2 and c-f['vwap']<=2*atr)
        elif self.id=='exhaustion_reversion':
            z=f.get('vwap_distance_z');vwap_exit=False
            ok=(z is not None and z<=-2*t and f['momentum_acceleration'] is not None and f['momentum_acceleration']>0 and
                f['previous_bar_volume_ratio'] is not None and f['previous_bar_volume_ratio']<1 and c>o and f['lower_wick_fraction']>=.25)
        if not ok:return self.reject('HYPOTHESIS_NOT_CONFIRMED')
        entry=h+p.offset;stop=l-p.offset
        if self.id=='exhaustion_reversion' and f['vwap']<entry+p.target_r*(entry-stop):return self.reject('INSUFFICIENT_REVERSION_ROOM')
        now=dt(frame.at)
        if self.id!='opening_drive':return self.signal(frame,session,entry,stop,'Research '+self.id,anchor=anchor,vwap_exit=vwap_exit)
        # Emitted for early signal diagnostics. Guarded replay still vetoes pre-ORB-window entry.
        if now<session.open+timedelta(minutes=5) or now>=session.close-timedelta(minutes=session.entry_cutoff_minutes):return self.reject('SESSION_WINDOW')
        key=f'{session.day}:{frame.symbol}:{self.id}:{self.fingerprint}:{anchor}'
        sig=CandidateSignal(self.id,self.version,self.fingerprint,frame.symbol,'LONG',frame.at,key,entry,stop,
            entry+p.target_r*(entry-stop),p.hold_minutes,p.target_r,True,frame.audit(),'Research opening drive',iso(now+timedelta(minutes=5)))
        return Evaluation(self.id,sig,'Research opening drive')


class Variant(Strategy):
    """One explicit old-family hypothesis; old classes/defaults remain untouched."""
    version='2.0.0-research'
    def __init__(self,name,parameters=None):
        super().__init__(parameters);self.base_id=name;self.id=name+'_variant'
        self.base=STRATEGIES[name](self.parameters)
    @property
    def fingerprint(self):return fingerprint(dict(base=self.base.fingerprint,id=self.id,version=self.version,features=FEATURES))
    def evaluate(self,frame,session):
        f=frame.values
        if self.base_id=='filtered_orb':
            # Remove joint gap/volatility/cross filters as an ablation, not a validated improvement.
            ok=f['price']>f['vwap'] and f.get('vwap_slope_5') is not None and f['vwap_slope_5']>0 and f.get('rvol_tod') is not None and f['rvol_tod']>=1
            original=STRATEGIES['orb'](self.parameters).evaluate(frame,session) if ok else self.reject('MINIMAL_FILTERS')
        else:original=self.base.evaluate(frame,session)
        if not original.signal:return self.reject(original.reason)
        checks={
            'orb':f.get('or15_prior_atr') is not None and .1<=f['or15_prior_atr']<=.5,
            'filtered_orb':True,
            'orb_retest':f['price']>f['vwap'] and f.get('previous_bar_volume_ratio') is not None and f['previous_bar_volume_ratio']<1,
            'vwap_trend':f.get('atr_14') is not None and 0<f['vwap_distance']<=f['atr_14'],
            'vwap_reversion':f.get('vwap_distance_z') is not None and f['vwap_distance_z']<=-2.5,
            'gap_continuation':f.get('gap_fill_fraction') is not None and f['gap_fill_fraction']<=.25,
            'gap_fade':f.get('body_fraction') is not None and f['body_fraction']>=.5,
        }
        if not checks[self.base_id]:return self.reject('DECLARED_VARIANT_FILTER')
        sig=original.signal
        return Evaluation(self.id,replace(sig,strategy_id=self.id,strategy_version=self.version,fingerprint=self.fingerprint,
            setup_id=sig.setup_id+':variant:'+self.fingerprint,reason='Declared '+self.id),self.id)


class ExitVariant(Strategy):
    version='2.0.0-research'
    def __init__(self,base,kind):
        if kind not in ('target15','hold20','structure'):raise Unsafe('Unknown exit comparison')
        self.base=base;self.kind=kind;self.id=base.id+'_'+kind;self.parameters=base.parameters
    @property
    def fingerprint(self):return fingerprint(dict(base=self.base.fingerprint,exit=self.kind,version=self.version))
    def evaluate(self,frame,session):
        result=self.base.evaluate(frame,session)
        if not result.signal:return self.reject(result.reason)
        s=result.signal;target_r=1.5 if self.kind=='target15' else s.target_r
        if self.kind=='structure':
            f=frame.values
            if self.base.id in ('gap_fade',):target=f.get('previous_close')
            elif self.base.id=='failed_breakout':target=(f['or15_high']+f['or15_low'])/2
            elif self.base.id in ('vwap_reversion','exhaustion_reversion'):target=f['vwap']
            else:target=s.entry+1.5*f['atr_14'] if f.get('atr_14') else None
            if target is None or target<=s.entry:return self.reject('STRUCTURAL_TARGET_UNAVAILABLE')
            target_r=(target-s.entry)/(s.entry-s.stop)
            if target_r<1:return self.reject('STRUCTURAL_REWARD_LESS_THAN_1R')
        s=replace(s,strategy_id=self.id,strategy_version=self.version,fingerprint=self.fingerprint,
            setup_id=s.setup_id+':exit:'+self.kind,target_r=target_r,target=s.entry+target_r*(s.entry-s.stop),
            max_hold_minutes=20 if self.kind=='hold20' else s.max_hold_minutes)
        return Evaluation(self.id,s,'Independent exit hypothesis '+self.kind)
