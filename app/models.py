"""Data model.

subjects        people in the cohort (observational) or under management (clients)
inputs          things that exist or were done for a subject (LinkedIn, filing, team page...)
targets         sites that may publish a page about a person
checks          one observation: did target T have a page for subject S on date D
serp_snapshots  raw SERP for a subject's bare-name query, for the rank model

Everything is dated so lags can be measured. Nothing is ever overwritten;
new observations are appended.
"""
from datetime import datetime
from sqlalchemy import (Column, Integer, String, Boolean, DateTime, Float, Text,
                        ForeignKey, UniqueConstraint, Index, JSON)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Subject(Base):
    __tablename__ = "subjects"
    id = Column(Integer, primary_key=True)
    full_name = Column(String(200), nullable=False)
    country = Column(String(2))                 # ISO
    company_name = Column(String(300))
    company_domain = Column(String(200))
    title = Column(String(200))
    cohort = Column(String(50), nullable=False) # companies_house, opencorporates_us, client, csv:<name>
    source_ref = Column(String(300))            # officer id, registry url, etc
    is_client = Column(Boolean, default=False)
    consented = Column(Boolean, default=False)  # controlled-study subjects only
    created_at = Column(DateTime, default=datetime.utcnow)
    meta = Column(JSON, default=dict)

    inputs = relationship("Input", back_populates="subject", cascade="all, delete-orphan")
    checks = relationship("Check", back_populates="subject", cascade="all, delete-orphan")

    __table_args__ = (Index("ix_subject_cohort", "cohort"),
                      Index("ix_subject_name", "full_name"))


class InputType(Base):
    """Catalogue of input kinds. Seeded from config/inputs.yaml."""
    __tablename__ = "input_types"
    key = Column(String(50), primary_key=True)   # linkedin, team_page, ch_appointment...
    label = Column(String(100))
    controllable = Column(Boolean, default=True) # can we do this for a client?
    noise = Column(Integer, default=1)           # 1 quiet .. 5 loud (PR)
    region = Column(String(20), default="global") # GB, US, EU, global
    description = Column(Text)


class Input(Base):
    __tablename__ = "inputs"
    id = Column(Integer, primary_key=True)
    subject_id = Column(Integer, ForeignKey("subjects.id"), nullable=False)
    type_key = Column(String(50), ForeignKey("input_types.key"), nullable=False)
    present = Column(Boolean, default=True)
    url = Column(String(500))
    observed_at = Column(DateTime, default=datetime.utcnow)  # when we saw it
    effective_at = Column(DateTime)                           # when it came into being, if known
    meta = Column(JSON, default=dict)

    subject = relationship("Subject", back_populates="inputs")
    __table_args__ = (Index("ix_input_subject_type", "subject_id", "type_key"),)


class Target(Base):
    """Sites that publish person pages. Seeded from config/targets.yaml."""
    __tablename__ = "targets"
    key = Column(String(50), primary_key=True)
    label = Column(String(100))
    domain = Column(String(200), nullable=False)
    category = Column(String(50))               # b2b, registry, investor, directory, people_search
    serp_query = Column(String(300))            # "{name} site:{domain}" etc
    url_pattern = Column(String(300))           # regex a person page URL must match
    direct_fetch = Column(Boolean, default=False)
    also_input = Column(String(50))             # if a page here is itself an input (crunchbase)
    region = Column(String(50))
    notes = Column(Text)
    active = Column(Boolean, default=True)


class Check(Base):
    __tablename__ = "checks"
    id = Column(Integer, primary_key=True)
    subject_id = Column(Integer, ForeignKey("subjects.id"), nullable=False)
    target_key = Column(String(50), ForeignKey("targets.key"), nullable=False)
    checked_at = Column(DateTime, default=datetime.utcnow)
    present = Column(Boolean, nullable=False)
    url = Column(String(500))
    richness = Column(Integer, default=0)       # 0 none, 1 stub, 2 basic, 3 full
    indexed = Column(Boolean)                   # found via Google SERP
    serp_position = Column(Integer)             # position for the bare-name query, if any
    method = Column(String(20))                 # serp, fetch
    raw = Column(JSON)                          # title/snippet/fields found

    subject = relationship("Subject", back_populates="checks")
    __table_args__ = (Index("ix_check_subject_target", "subject_id", "target_key"),
                      Index("ix_check_target_present", "target_key", "present"))


class SerpSnapshot(Base):
    """Bare-name SERP for a subject: which domains rank, where. Feeds the rank model."""
    __tablename__ = "serp_snapshots"
    id = Column(Integer, primary_key=True)
    subject_id = Column(Integer, ForeignKey("subjects.id"), nullable=False)
    fetched_at = Column(DateTime, default=datetime.utcnow)
    query = Column(String(300))
    location_code = Column(Integer)
    results = Column(JSON)                      # [{position, domain, url, title}]
    __table_args__ = (Index("ix_serp_subject", "subject_id"),)


class Edge(Base):
    """Computed: input -> target with empirical weight. Rebuilt by analysis.run()."""
    __tablename__ = "edges"
    id = Column(Integer, primary_key=True)
    computed_at = Column(DateTime, default=datetime.utcnow)
    input_key = Column(String(50), nullable=False)
    target_key = Column(String(50), nullable=False)
    n_with = Column(Integer)        # subjects with the input
    n_without = Column(Integer)
    p_with = Column(Float)          # P(target page | input present)
    p_without = Column(Float)       # P(target page | input absent)
    lift = Column(Float)            # p_with / p_without
    median_lag_days = Column(Float) # from controlled subjects, when known
    n_lag = Column(Integer)
    __table_args__ = (UniqueConstraint("input_key", "target_key", name="uq_edge"),)


class TargetStat(Base):
    """Computed per target: how often it reaches page one for a bare-name query."""
    __tablename__ = "target_stats"
    target_key = Column(String(50), primary_key=True)
    computed_at = Column(DateTime, default=datetime.utcnow)
    n_subjects_checked = Column(Integer)
    presence_rate = Column(Float)
    page_one_rate = Column(Float)   # of subjects with a page, share where it ranks top 10
    page_two_rate = Column(Float)
    median_position = Column(Float)
