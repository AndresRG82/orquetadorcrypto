import numpy as np
import pandas as pd


def compute_ic_series(factor_df: pd.DataFrame, return_df: pd.DataFrame) -> pd.Series:
    aligned = factor_df.align(return_df, join="inner", axis=0)
    factor_aligned, return_aligned = aligned
    dates = factor_aligned.index
    ic_values = []
    for dt in dates:
        f = factor_aligned.loc[dt].dropna()
        r = return_aligned.loc[dt].dropna()
        common = f.index.intersection(r.index)
        if len(common) < 5:
            ic_values.append(np.nan)
            continue
        f_sub = f[common].rank()
        r_sub = r[common].rank()
        f_mean = f_sub.mean()
        r_mean = r_sub.mean()
        num = ((f_sub - f_mean) * (r_sub - r_mean)).sum()
        den = np.sqrt(
            ((f_sub - f_mean) ** 2).sum() * ((r_sub - r_mean) ** 2).sum()
        )
        ic = num / den if den > 0 else np.nan
        ic_values.append(ic)
    return pd.Series(ic_values, index=dates, name="ic")


def compute_group_equity(
    factor_df: pd.DataFrame,
    return_df: pd.DataFrame,
    n_groups: int = 5,
) -> pd.DataFrame:
    aligned = factor_df.align(return_df, join="inner", axis=0)
    factor_aligned, return_aligned = aligned
    dates = factor_aligned.index
    group_navs = {g: [1.0] for g in range(n_groups)}
    for i, dt in enumerate(dates):
        if i == 0:
            continue
        f = factor_aligned.loc[dt].dropna()
        r = return_aligned.loc[dt].dropna()
        common = f.index.intersection(r.index)
        if len(common) < n_groups * 2:
            for g in range(n_groups):
                group_navs[g].append(group_navs[g][-1])
            continue
        f_sub = f[common]
        r_sub = r[common]
        labels = pd.qcut(
            f_sub.rank(),
            q=n_groups,
            labels=list(range(n_groups)),
            duplicates="drop",
        )
        for g in range(n_groups):
            mask = labels == g
            if mask.sum() > 0:
                group_return = r_sub[mask].mean()
                group_navs[g].append(group_navs[g][-1] * (1 + group_return))
            else:
                group_navs[g].append(group_navs[g][-1])
    result = pd.DataFrame(
        {f"group_{g}": navs for g, navs in group_navs.items()},
        index=dates,
    )
    return result
