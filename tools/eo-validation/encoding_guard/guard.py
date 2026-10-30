"""Offline paired-encoding research diagnostic; never authorizes correction."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import numpy as np


class Rejected(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise Rejected(reason)


def numeric(value):
    if not isinstance(value, (float, int)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        return False


def finite_on_support(values, support, reason):
    require(bool(np.all(np.isfinite(values[support]))), reason)


def quality(red, nir, invalid, scl, epsilon=1e-12):
    """Keep negative reflectance; reject overflow on otherwise valid support.

    Nonfinite input remains an explicit quality exclusion. Finite input that
    overflows during NDVI arithmetic is rejected, never converted into a ratio.
    """
    finite = np.isfinite(red) & np.isfinite(nir)
    supported = ~invalid & finite
    with np.errstate(over='ignore', invalid='ignore'):
        denominator = red + nir
        numerator = nir - red
    finite_on_support(denominator, supported, 'nonfinite NDVI denominator')
    finite_on_support(numerator, supported, 'nonfinite NDVI numerator')
    near_zero = finite & (np.abs(denominator) <= epsilon)
    usable = supported & ~near_zero
    ndvi = np.full(red.shape, np.nan)
    with np.errstate(over='ignore', invalid='ignore', divide='ignore'):
        np.divide(numerator, denominator, out=ndvi, where=usable)
    finite_on_support(ndvi, usable, 'nonfinite NDVI result')
    flags = {
        'nodata': invalid,
        'nonfinite_reflectance': ~finite,
        'negative_reflectance': ~invalid & finite & ((red < 0) | (nir < 0)),
        'near_zero_denominator_numerical_only': ~invalid & near_zero,
        'scl_cloud': np.isin(scl, [8, 9, 10]),
        'scl_shadow': scl == 3,
        'scl_nodata_or_defective': np.isin(scl, [0, 1]),
        'scl_invalid_label': ~np.isin(scl, list(range(12))),
        'outside_minus1_plus1': usable & (np.abs(ndvi) > 1),
    }
    return ndvi, flags


def compare(root):
    root = Path(root)
    names = ['old_item.json', 'c1_item.json', 'window_metadata.json']
    names += [f'{k}_{b}.npz' for k in ['old', 'c1'] for b in ['red', 'nir', 'scl']]
    before = {name: hashlib.sha256((root/name).read_bytes()).hexdigest() for name in names}
    items = {k: json.loads((root/f'{k}_item.json').read_text()) for k in ['old', 'c1']}
    meta = json.loads((root/'window_metadata.json').read_text())
    for prop in ['s2:product_uri', 's2:datastrip_id']:
        value = items['old']['properties'].get(prop)
        require(isinstance(value, str) and bool(value.strip()), 'missing identity: '+prop)
        require(value == items['c1']['properties'].get(prop), 'identity mismatch: '+prop)
    raw, invalid, encodings, scl = {}, {}, {}, {}
    shape = None
    grid = None
    full_grid = None
    for k, item in items.items():
        with np.load(root/f'{k}_scl.npz', allow_pickle=False) as z:
            scl[k] = z['aligned'].copy()
            scl_source = z['source'].copy()
        for band in ['red', 'nir']:
            key = f'{k}_{band}'
            asset = item['assets'][band]
            declaration = asset.get('raster:bands', [{}])[0]
            for field in ['scale', 'offset', 'nodata']:
                require(numeric(declaration.get(field)), f'{key}: missing/nonfinite {field}')
            require(declaration['scale'] > 0, key+': nonpositive scale')
            encodings[key] = {f: declaration[f] for f in ['scale', 'offset', 'nodata']}
            require(meta[key]['url'] == asset['href'], key+': source URL mismatch')
            t = meta[key]['transform']
            require(len(t) == 9 and all(numeric(x) for x in t), key+': invalid transform')
            require(t[6:] == [0, 0, 1] and t[0]*t[4]-t[1]*t[3] != 0, key+': singular transform')
            current = (meta[key]['crs'], tuple(t), tuple(meta[key]['window']))
            require(bool(current[0]), key+': missing CRS')
            if grid is None:
                grid = current
            require(grid == current, key+': window grid mismatch')
            epsg = item['properties'].get('proj:epsg')
            require(type(epsg) is int and epsg > 0, key+': missing/invalid EPSG')
            asset_epsg = asset.get('proj:epsg', epsg)
            require(type(asset_epsg) is int and asset_epsg == epsg,
                    key+': asset CRS declaration mismatch')
            require(current[0] == f'EPSG:{epsg}', key+': CRS declaration mismatch')
            require('proj:shape' in asset and 'proj:transform' in asset, key+': missing full grid')
            whole = (epsg, tuple(asset['proj:shape']), tuple(asset['proj:transform']))
            require(len(whole[1]) == 2 and all(isinstance(x, int) and x > 0 for x in whole[1]), key+': invalid full shape')
            require(len(whole[2]) == 6 and all(numeric(x) for x in whole[2]), key+': invalid full transform')
            if full_grid is None:
                full_grid = whole
            require(full_grid == whole, key+': full grid mismatch')
            col, row, width, height = meta[key]['window']
            require(all(numeric(x) and int(x) == x for x in [col, row, width, height]), key+': invalid window coordinates')
            require(col >= 0 and row >= 0 and width > 0 and height > 0 and col+width <= whole[1][1] and row+height <= whole[1][0], key+': window outside full grid')
            a0,b0,c0,d0,e0,f0 = whole[2]
            expected_transform = [a0,b0,c0+a0*col+b0*row,d0,e0,f0+d0*col+e0*row,0,0,1]
            require(np.allclose(t, expected_transform, rtol=0, atol=1e-8), key+': window/full transform conflict')
            with np.load(root/f'{key}.npz', allow_pickle=False) as z:
                a, m = z['data'], z['mask']
                require(a.ndim == 2 and a.dtype.kind in 'uif', key+': invalid array')
                require(m.shape == a.shape and m.dtype.kind == 'b', key+': invalid mask')
                if shape is None:
                    shape = a.shape
                require(a.shape == shape, key+': array shape mismatch')
                require(tuple(meta[key]['window'][2:][::-1]) == a.shape, key+': window shape mismatch')
                raw[key] = a.astype('float64')
                invalid[key] = m | (a == declaration['nodata'])
        require(scl[k].shape == shape and scl[k].dtype.kind in 'ui', k+': SCL shape/type mismatch')
        require(meta[k+'_scl']['target_transform'] == list(grid[1]), k+': SCL target mismatch')
        require(meta[k+'_scl']['resampling'] == 'nearest', k+': SCL resampling mismatch')
        require(meta[k+'_scl']['url'] == item['assets']['scl']['href'], k+': SCL source URL mismatch')
        st = meta[k+'_scl']['source_transform']
        require(len(st) == 9 and all(numeric(x) for x in st) and st[6:] == [0,0,1], k+': invalid SCL source transform')
        sa = item['assets']['scl']
        sw = meta[k+'_scl']['source_window']
        require(len(sw) == 4 and all(numeric(x) and int(x) == x for x in sw), k+': invalid SCL source window')
        sc, sr, width, height = sw
        fs, ft = sa.get('proj:shape'), sa.get('proj:transform')
        require(isinstance(fs,list) and len(fs) == 2 and all(isinstance(x,int) and x > 0 for x in fs), k+': invalid full SCL shape')
        require(isinstance(ft,list) and len(ft) == 6 and all(numeric(x) for x in ft), k+': invalid full SCL transform')
        require(sc >= 0 and sr >= 0 and width > 0 and height > 0 and sc+width <= fs[1] and sr+height <= fs[0], k+': SCL window outside full grid')
        require(scl_source.shape == (height,width), k+': SCL source window shape mismatch')
        scl_epsg = sa.get('proj:epsg', epsg)
        require(type(scl_epsg) is int and scl_epsg == epsg, k+': SCL CRS mismatch')
        a0,b0,c0,d0,e0,f0 = ft
        expected = [a0,b0,c0+a0*sc+b0*sr,d0,e0,f0+d0*sc+e0*sr,0,0,1]
        require(np.allclose(st, expected, rtol=0, atol=1e-8), k+': SCL window/full transform conflict')
        matrix = np.array([[st[0],st[1]],[st[3],st[4]]])
        require(np.linalg.det(matrix) != 0, k+': singular SCL source transform')
        rr, cc = np.indices(shape, dtype=float)
        target = np.array(grid[1]).reshape(3,3)
        xy = target[:2,:2] @ np.array([cc.ravel()+.5,rr.ravel()+.5]) + target[:2,2:3]
        cells = np.floor(np.linalg.solve(matrix, xy-np.array([[st[2]],[st[5]]]))).astype(int)
        require(scl_source.ndim == 2 and scl_source.dtype.kind in 'ui', k+': invalid native SCL')
        require(bool(np.all((cells[0]>=0)&(cells[1]>=0)&(cells[0]<scl_source.shape[1])&(cells[1]<scl_source.shape[0]))), k+': SCL source coverage mismatch')
        require(np.array_equal(scl_source[cells[1],cells[0]].reshape(shape), scl[k]), k+': aligned SCL differs from source nearest cells')
    base = np.ones(shape, dtype=bool)
    for key in raw:
        base &= ~invalid[key] & np.isfinite(raw[key])
    variants, quality_counts, decoded = {}, {}, {}
    for name, k, use_offset in [('c1_declared', 'c1', True), ('old_declared', 'old', True), ('old_scale_only_hypothesis', 'old', False)]:
        vals = []
        for band in ['red', 'nir']:
            key = f'{k}_{band}'
            d = encodings[key]
            with np.errstate(over='ignore', invalid='ignore'):
                value = raw[key]*float(d['scale']) + (float(d['offset']) if use_offset else 0)
            support = ~invalid[key] & np.isfinite(raw[key])
            finite_on_support(value, support, key+': nonfinite decoded reflectance')
            vals.append(value)
        decoded[name] = vals
        a, flags = quality(*vals, invalid[k+'_red'] | invalid[k+'_nir'], scl[k])
        variants[name] = a
        quality_counts[name] = {f: int(mask.sum()) for f, mask in flags.items()}
    common = base.copy()
    for a in variants.values():
        common &= np.isfinite(a)
    def stats(a, b):
        if not common.any():
            return None
        with np.errstate(over='ignore', invalid='ignore'):
            d = a[common]-b[common]
            result = {'rmse': float(np.sqrt(np.mean(d*d))),
                      'max_absolute': float(np.max(abs(d))),
                      'median_absolute': float(np.median(abs(d))),
                      'p95_absolute': float(np.percentile(abs(d), 95))}
        require(all(numeric(value) for value in result.values()), 'nonfinite NDVI statistics')
        return result
    residuals = {}
    all_match = True
    for index, band in enumerate(['red', 'nir']):
        with np.errstate(over='ignore', invalid='ignore'):
            delta = decoded['c1_declared'][index] - decoded['old_scale_only_hypothesis'][index]
        finite_on_support(delta, base, band+': nonfinite reflectance residual')
        residual = base & (np.abs(delta) > 1e-12)
        all_match &= not bool(residual.any())
        residuals[band] = {'support_pixels': int(base.sum()), 'scale_only_residual_pixels': int(residual.sum()), 'residual_old_dn_equal_one': int((residual & (raw['old_'+band] == 1)).sum()), 'max_reflectance_residual': float(np.max(abs(delta[base]))) if base.any() else None}
    flag = items['old']['properties'].get('earthsearch:boa_offset_applied')
    require(flag is None or type(flag) is bool, 'invalid boa_offset_applied type')
    flag_assessment = 'unknown_flag_no_encoding_inference'
    if flag is True:
        flag_assessment = 'flag_declares_prior_application_reapplying_offset_hazard'
    elif flag is False:
        flag_assessment = ('flag_inconsistent_with_exact_pair_scale_only_equivalence' if all_match and base.any() else 'pair_residuals_present_no_automatic_flag_resolution')
    after = {name: hashlib.sha256((root/name).read_bytes()).hexdigest() for name in names}
    require(before == after, 'input files changed during diagnostic')
    return {'status': 'DIAGNOSTIC_ONLY', 'automatic_correction_authorized': False,
            'physical_accuracy_verified': False, 'scope': 'same-product paired windows; C1 is a comparator, not ground truth',
            'product_uri': items['old']['properties']['s2:product_uri'], 'shape': list(shape),
            'total_pixels': int(np.prod(shape)), 'common_raw_valid_pixels': int(base.sum()),
            'common_numeric_pixels': int(common.sum()), 'numeric_exclusions_after_raw_mask': int((base & ~common).sum()),
            'source_invalid_or_nonfinite_pixels': int((~base).sum()), 'old_boa_offset_applied': flag,
            'flag_assessment': flag_assessment, 'encodings': encodings, 'band_residuals': residuals,
            'comparisons': {name: stats(a, variants['c1_declared']) for name, a in variants.items() if name != 'c1_declared'},
            'quality_flag_counts_overlapping': quality_counts,
            'scl_agreement_fraction': float(np.mean(scl['old'] == scl['c1'])),
            'scl_vegetation_pixels': {k: int((a == 4).sum()) for k, a in scl.items()},
            'input_sha256': before, 'inputs_unchanged_during_run': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sample_directory', type=Path)
    args = parser.parse_args()
    try:
        result = compare(args.sample_directory)
        output = json.dumps(result, indent=2, allow_nan=False)
    except (Rejected, KeyError, OSError, ValueError, IndexError, TypeError, ArithmeticError) as exc:
        print(json.dumps({'status': 'REJECTED', 'reason': str(exc), 'automatic_correction_authorized': False}))
        return 2
    print(output)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
