from src.domain.models.market_target import MarketTarget


def target(id='apple-mac-mini-m6',ram=16,ssd=256):
    return MarketTarget.from_task({'product_selection':dict(catalog_id=id,memory_gb=ram,storage_gb=ssd)})


def analysis(**updates):
    return dict(match_status='match',product_family='mac mini',chip='M6',memory_gb=16,storage_gb=256,
                cpu_cores=None,gpu_cores=None,identity_evidence='模型说明文字，不等于原文',
                evidence_source='text',**updates)


def test_summary_and_short_literal_excerpts_are_separate():
    title='个人自用Mac mini M6 16+256，国行正品，原箱'
    data=analysis(evidence_quotes=[dict(source='title',text=title)])
    result=target().validate_identity(data,{'商品标题':title})
    assert result['match_status']=='match'
    assert result['identity_evidence']=='模型说明文字，不等于原文'
    assert result['verified_evidence']==[dict(source='title',text=title)]
    assert result['cpu_cores'] is None and result['gpu_cores'] is None
    assert 'catalog_derived_fields' not in result


def test_legacy_narrative_grounds_only_real_quoted_fragments():
    title='个人自用Mac mini M6 16+256，国行正品，原箱'
    data=analysis()
    data['identity_evidence']=f'标题“{title}”；描述“顶版配置M6芯片、16G内存、256G SSD”；包装图“M6芯片/16GB内存”。'
    result=target().validate_identity(data,{'商品标题':title,'商品描述':'顶版配置M6芯片、16G内存、256G SSD'})
    assert result['match_status']=='match'
    assert len(result['verified_evidence'])==2
    assert all(q['source']!='image' for q in result['verified_evidence'])


def test_changing_memory_is_not_changing_the_sold_model():
    title='mac mini m6 16+256，99成新，准备换个内存'
    data=analysis(evidence_quotes=[dict(source='title',text=title)])
    assert target().validate_identity(data,{'商品标题':title})['match_status']=='match'
    data['identity_evidence']=title
    data.pop('evidence_quotes')
    assert target().validate_identity(data,{'商品标题':title})['match_status']=='match'


def test_replacement_model_is_not_evidence_for_current_sale():
    title='Mac mini M4 16+256，因为换了Mac mini M6所以卖M4'
    data=analysis(evidence_quotes=[dict(source='title',text='Mac mini M6')])
    assert target().validate_identity(data,{'商品标题':title})['match_status']=='unknown'
    data['chip']='M4';data['match_status']='no_match'
    assert target().validate_identity(data,{'商品标题':title})['match_status']=='no_match'


def test_invented_wrong_source_and_capacity_remain_excluded():
    title='Mac mini M4 16+256'
    for entry in [dict(source='title',text='Mac mini M6'),dict(source='description',text=title),dict(source='image',text='16GB256GB')]:
        result=target().validate_identity(analysis(evidence_quotes=[entry]),{'商品标题':title})
        assert result['match_status']!='match'
    data=analysis(evidence_quotes=[dict(source='title',text='Mac mini M6 16+512')])
    data['storage_gb']=512
    assert target().validate_identity(data,{'商品标题':'Mac mini M6 16+512'})['match_status']=='no_match'


def test_core_counts_never_gate_even_pro_targets():
    title='Mac mini M4 Pro 24+512'
    data=analysis(evidence_quotes=[dict(source='title',text=title)])
    data.update(chip='M4 Pro',memory_gb=24,storage_gb=512)
    result=target('apple-mac-mini-m4-pro-12-16',24,512).validate_identity(data,{'商品标题':title})
    assert result['match_status']=='match'
    explicit=analysis(evidence_quotes=[dict(source='title',text='Mac mini M6 16+256')])
    explicit['cpu_cores']=10
    assert target().validate_identity(explicit,{'商品标题':'Mac mini M6 16+256'})['match_status']=='match'
