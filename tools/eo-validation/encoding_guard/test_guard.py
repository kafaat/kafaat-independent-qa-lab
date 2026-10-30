import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from guard import compare, quality, Rejected

BASE = Path(__file__).resolve().parent.parent


class GuardTests(unittest.TestCase):
    def fixture(self):
        temp = tempfile.TemporaryDirectory(prefix='eo-guard-test-')
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        src = BASE/'yemen_december_fixed_window'
        for name in ['old_item.json','c1_item.json','window_metadata.json'] + [f'{k}_{b}.npz' for k in ['old','c1'] for b in ['red','nir','scl']]:
            shutil.copyfile(src/name, root/name)
        return root

    def change_json(self, root, name, function):
        p = root/name
        value = json.loads(p.read_text()); function(value)
        p.write_text(json.dumps(value))

    def test_real_three_samples(self):
        expected = {'yemen': (0, 0), 'yemen_march_selected': (7, 7), 'yemen_december_fixed_window': (0, 0)}
        for name, (residual, negatives) in expected.items():
            with self.subTest(sample=name):
                r = compare(BASE/name)
                self.assertEqual(r['status'], 'DIAGNOSTIC_ONLY')
                self.assertFalse(r['automatic_correction_authorized'])
                self.assertFalse(r['physical_accuracy_verified'])
                self.assertEqual(r['band_residuals']['red']['scale_only_residual_pixels'], residual)
                self.assertEqual(r['band_residuals']['red']['residual_old_dn_equal_one'], residual)
                self.assertEqual(r['quality_flag_counts_overlapping']['c1_declared']['negative_reflectance'], negatives)
                original = json.loads((BASE/name/'yemen_comparison.json').read_text())
                self.assertAlmostEqual(r['comparisons']['old_declared']['rmse'], original['unfiltered']['old_metadata']['rmse'], places=12)
                self.assertEqual(len(r['input_sha256']),9)

    def test_identity_mismatch(self):
        p=self.fixture(); self.change_json(p,'c1_item.json',lambda x:x['properties'].update({'s2:product_uri':'different'}))
        with self.assertRaisesRegex(Rejected,'identity mismatch'): compare(p)

    def test_empty_identity(self):
        p=self.fixture()
        for name in ['old_item.json','c1_item.json']:
            self.change_json(p,name,lambda x:x['properties'].update({'s2:product_uri':''}))
        with self.assertRaisesRegex(Rejected,'missing identity'): compare(p)

    def test_window_grid_mismatch(self):
        p=self.fixture(); self.change_json(p,'window_metadata.json',lambda x:x['c1_red']['transform'].__setitem__(2,477950))
        with self.assertRaisesRegex(Rejected,'grid mismatch'): compare(p)

    def test_joint_window_metadata_lie(self):
        p=self.fixture()
        def alter(x):
            for k in ['old_red','old_nir','c1_red','c1_nir']: x[k]['transform'][2]+=10
        self.change_json(p,'window_metadata.json',alter)
        with self.assertRaisesRegex(Rejected,'window/full transform conflict'): compare(p)

    def test_full_grid_mismatch(self):
        p=self.fixture(); self.change_json(p,'c1_item.json',lambda x:x['assets']['red']['proj:shape'].__setitem__(0,10981))
        with self.assertRaisesRegex(Rejected,'full grid mismatch'): compare(p)

    def test_shape_mismatch(self):
        p=self.fixture(); np.savez(p/'old_red.npz',data=np.ones((2,2)),mask=np.zeros((2,2),dtype=bool))
        with self.assertRaisesRegex(Rejected,'window shape mismatch'): compare(p)

    def test_missing_scale_rejected(self):
        p=self.fixture(); self.change_json(p,'old_item.json',lambda x:x['assets']['red']['raster:bands'][0].pop('scale'))
        with self.assertRaisesRegex(Rejected,'missing/nonfinite scale'): compare(p)

    def test_nonfinite_offset_rejected(self):
        p=self.fixture(); self.change_json(p,'old_item.json',lambda x:x['assets']['red']['raster:bands'][0].update(offset=float('nan')))
        with self.assertRaisesRegex(Rejected,'missing/nonfinite offset'): compare(p)

    def test_unknown_flag_is_not_default_false(self):
        p=self.fixture(); self.change_json(p,'old_item.json',lambda x:x['properties'].pop('earthsearch:boa_offset_applied'))
        r=compare(p)
        self.assertIsNone(r['old_boa_offset_applied'])
        self.assertEqual(r['flag_assessment'],'unknown_flag_no_encoding_inference')

    def test_all_nodata_has_null_statistics(self):
        p=self.fixture()
        np.savez(p/'old_red.npz',data=np.zeros((256,256),dtype='uint16'),mask=np.zeros((256,256),dtype=bool))
        r=compare(p)
        self.assertEqual(r['common_numeric_pixels'],0)
        self.assertIsNone(r['comparisons']['old_declared'])
        self.assertIsNone(r['band_residuals']['red']['max_reflectance_residual'])

    def test_nan_and_nodata_excluded_independently(self):
        p=self.fixture()
        z=np.load(p/'old_red.npz'); a=z['data'].astype(float); m=z['mask'];z.close()
        a[0,0]=np.nan;a[0,1]=0
        np.savez(p/'old_red.npz',data=a,mask=m)
        r=compare(p)
        self.assertEqual(r['source_invalid_or_nonfinite_pixels'],2)
        self.assertEqual(r['quality_flag_counts_overlapping']['old_declared']['nonfinite_reflectance'],1)
        self.assertEqual(r['quality_flag_counts_overlapping']['old_declared']['nodata'],1)

    def test_negative_and_zero_denominator_not_clamped(self):
        r=np.array([[-.1, .1, .2]])
        n=np.array([[.3,-.1,.3]])
        a, flags=quality(r,n,np.zeros(r.shape,bool),np.array([[4,4,9]],dtype='uint8'))
        self.assertAlmostEqual(a[0,0],2)
        self.assertTrue(np.isnan(a[0,1]))
        self.assertTrue(flags['negative_reflectance'][0,0])
        self.assertFalse(flags['nodata'][0,0])
        self.assertTrue(flags['near_zero_denominator_numerical_only'][0,1])
        self.assertTrue(flags['scl_cloud'][0,2])
        self.assertAlmostEqual(a[0,2],.2)

    def test_scl_alignment_rejected(self):
        p=self.fixture(); z=np.load(p/'old_scl.npz'); source=z['source'];aligned=z['aligned'].copy();z.close()
        aligned[0,0]=9 if aligned[0,0]!=9 else 4
        np.savez(p/'old_scl.npz',source=source,aligned=aligned)
        with self.assertRaisesRegex(Rejected,'aligned SCL differs'): compare(p)

    def test_scl_full_transform_mismatch(self):
        p=self.fixture(); self.change_json(p,'c1_item.json',lambda x:x['assets']['scl']['proj:transform'].__setitem__(2,399980))
        with self.assertRaisesRegex(Rejected,'SCL window/full transform conflict'): compare(p)

    def test_scl_source_window_mismatch(self):
        p=self.fixture(); self.change_json(p,'window_metadata.json',lambda x:x['c1_scl']['source_window'].__setitem__(0,3900))
        with self.assertRaisesRegex(Rejected,'SCL window/full transform conflict'): compare(p)

    def test_input_changed_during_run_rejected(self):
        p=self.fixture(); real=Path.read_bytes; seen={}
        def altered(path):
            data=real(path)
            if path==p/'old_item.json':
                seen[str(path)]=seen.get(str(path),0)+1
                if seen[str(path)]>1: return data+b' '
            return data
        with patch.object(Path,'read_bytes',altered):
            with self.assertRaisesRegex(Rejected,'input files changed'): compare(p)

    def test_asset_crs_conflicts_rejected(self):
        for kind in ('old', 'c1'):
            for band in ('red', 'nir', 'scl'):
                with self.subTest(kind=kind, band=band):
                    p = self.fixture()
                    self.change_json(p, kind+'_item.json',
                                     lambda d: d['assets'][band].update({'proj:epsg': 32639}))
                    with self.assertRaisesRegex(Rejected, 'CRS.*mismatch'):
                        compare(p)

    def test_matching_asset_crs_overrides_accepted(self):
        p = self.fixture()
        def explicit_crs(item):
            for band in ('red', 'nir', 'scl'):
                item['assets'][band]['proj:epsg'] = item['properties']['proj:epsg']
        for kind in ('old', 'c1'):
            self.change_json(p, kind+'_item.json', explicit_crs)
        result = compare(p)
        self.assertEqual(result['status'], 'DIAGNOSTIC_ONLY')
        self.assertFalse(result['automatic_correction_authorized'])
        self.assertGreater(result['common_numeric_pixels'], 0)
        json.dumps(result, allow_nan=False)

    def test_malformed_asset_crs_overrides_rejected(self):
        for kind in ('old', 'c1'):
            for band in ('red', 'nir', 'scl'):
                for value in (None, True, 32638.0, '32638'):
                    with self.subTest(kind=kind, band=band, value=value):
                        p = self.fixture()
                        self.change_json(p, kind+'_item.json',
                                         lambda d: d['assets'][band].update({'proj:epsg': value}))
                        with self.assertRaisesRegex(Rejected, 'CRS.*mismatch'):
                            compare(p)

    def test_huge_integer_encoding_fields_rejected(self):
        for field in ('scale', 'offset', 'nodata'):
            with self.subTest(field=field):
                p = self.fixture()
                self.change_json(p, 'old_item.json', lambda d:
                                 d['assets']['red']['raster:bands'][0].update({field: 10**1000}))
                with self.assertRaisesRegex(Rejected, 'missing/nonfinite '+field):
                    compare(p)

    def test_decoded_reflectance_overflow_rejected(self):
        for kind in ('old', 'c1'):
            for band in ('red', 'nir'):
                with self.subTest(kind=kind, band=band):
                    p = self.fixture()
                    self.change_json(p, kind+'_item.json', lambda d:
                                     d['assets'][band]['raster:bands'][0].update(scale=1e308))
                    with self.assertRaisesRegex(Rejected, kind+'_'+band+': nonfinite decoded reflectance'):
                        compare(p)

    def test_ndvi_arithmetic_overflow_rejected(self):
        for red, nir, operation in ((1e308, 1.5e308, 'denominator'),
                                    (-1e308, 1.5e308, 'numerator')):
            with self.subTest(operation=operation):
                with self.assertRaisesRegex(Rejected, 'nonfinite NDVI '+operation):
                    quality(np.array([[red]]), np.array([[nir]]),
                            np.array([[False]]), np.array([[4]], dtype='uint8'))

    def test_finite_ndvi_ratio_preserved(self):
        ndvi, flags = quality(np.array([[0.1]]), np.array([[0.15]]),
                              np.array([[False]]), np.array([[4]], dtype='uint8'))
        self.assertAlmostEqual(ndvi[0, 0], 0.2)
        self.assertTrue(all(not bool(mask[0, 0]) for mask in flags.values()))

    def test_empty_decoded_support_cannot_infer_equivalence(self):
        p = self.fixture()
        def overflow_encoding(item):
            for band in ('red', 'nir'):
                item['assets'][band]['raster:bands'][0].update(scale=1e308, offset=0)
        for kind in ('old', 'c1'):
            self.change_json(p, kind+'_item.json', overflow_encoding)
            for band in ('red', 'nir'):
                data = np.full((256, 256), 2000, dtype='uint16')
                np.savez(p/f'{kind}_{band}.npz', data=data, mask=np.zeros(data.shape, dtype=bool))
        with self.assertRaisesRegex(Rejected, 'nonfinite decoded reflectance'):
            compare(p)

    def test_cli_numeric_rejection_is_structured(self):
        for scale, reason in ((1e308, 'nonfinite decoded reflectance'),
                              (10**1000, 'missing/nonfinite scale')):
            with self.subTest(reason=reason):
                p = self.fixture()
                self.change_json(p, 'old_item.json', lambda d:
                                 d['assets']['red']['raster:bands'][0].update(scale=scale))
                env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
                result = subprocess.run([sys.executable, '-B', str(BASE/'encoding_guard/guard.py'), str(p)],
                                        capture_output=True, text=True, env=env, timeout=10)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stderr, '')
                report = json.loads(result.stdout)
                self.assertEqual(report['status'], 'REJECTED')
                self.assertIn(reason, report['reason'])
                self.assertFalse(report['automatic_correction_authorized'])
                json.dumps(report, allow_nan=False)


if __name__ == '__main__':
    unittest.main(verbosity=2)
