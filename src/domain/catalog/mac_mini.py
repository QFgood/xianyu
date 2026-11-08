"""Apple official specification snapshots, checked 2026-11-08.

Stable IDs identify an actual chip tier; never remap an existing ID to a new model.
Sources are metadata references only, not remotely executed configuration.
"""
REVISION = 'apple-mac-mini-2026-11-08-v1'
CURRENT = 'https://www.apple.com.cn/mac-mini/specs/'
M4 = 'https://support.apple.com/zh-cn/121555'
M2 = 'https://support.apple.com/zh-cn/111837'
M1 = 'https://support.apple.com/zh-cn/111894'


def model(id, chip, cpu, gpu, memory, storage, source, year=None):
    return dict(id=id, brand='Apple', product_family='mac mini', name=f'Mac mini {chip}',
                chip=chip, cpu_cores=cpu, gpu_cores=gpu, memory_options=memory,
                storage_options=storage, source_url=source, source_name='Apple 官方技术规格',
                checked_at='2026-11-08', year=year, revision=REVISION)


MODELS = (
    model('apple-mac-mini-m6', 'M6', 12, 12, [16,24,32], [256,512,1024,2048], CURRENT),
    model('apple-mac-mini-m5-pro-15-16', 'M5 Pro', 15, 16, [24,48,64], [512,1024,2048,4096,8192], CURRENT),
    model('apple-mac-mini-m5-pro-18-20', 'M5 Pro', 18, 20, [24,48,64], [512,1024,2048,4096,8192], CURRENT),
    model('apple-mac-mini-m4', 'M4', 10, 10, [16,24,32], [256,512,1024,2048], M4, 2024),
    model('apple-mac-mini-m4-pro-12-16', 'M4 Pro', 12, 16, [24,48,64], [512,1024,2048,4096,8192], M4, 2024),
    model('apple-mac-mini-m4-pro-14-20', 'M4 Pro', 14, 20, [24,48,64], [512,1024,2048,4096,8192], M4, 2024),
    model('apple-mac-mini-m2', 'M2', 8, 10, [8,16,24], [256,512,1024,2048], M2, 2023),
    model('apple-mac-mini-m2-pro-10-16', 'M2 Pro', 10, 16, [16,32], [512,1024,2048,4096,8192], M2, 2023),
    model('apple-mac-mini-m2-pro-12-19', 'M2 Pro', 12, 19, [16,32], [512,1024,2048,4096,8192], M2, 2023),
    model('apple-mac-mini-m1', 'M1', 8, 8, [8,16], [256,512,1024,2048], M1, 2020),
)
